"""WhatsApp Cloud API adapter.

Two things matter here and both have bitten people before:

1. Meta re-delivers a webhook for up to 72 hours if we do not answer 200 quickly. So we verify,
   enqueue, and return 200 immediately. The work happens after.
2. `wa_message_id` is the only reliable way to know we have seen a message before. It becomes
   the idempotency key and the database has a unique index on it.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

import httpx
import structlog

from app.channels.base import ChannelAdapter
from app.core.envelope import InboundTurn, OutboundAction
from app.settings import settings

log = structlog.get_logger()
GRAPH = "https://graph.facebook.com/v21.0"


class WhatsAppAdapter(ChannelAdapter):
    name = "whatsapp"

    def __init__(self, phone_number_id: str, access_token: str) -> None:
        self.phone_number_id = phone_number_id
        self.access_token = access_token

    async def verify(self, headers: dict, body: bytes) -> bool:
        sent = headers.get("x-hub-signature-256", "")
        if not sent.startswith("sha256="):
            return False
        expected = hmac.new(
            settings.whatsapp_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sent.removeprefix("sha256="), expected)

    async def normalise(self, payload: dict, tenant_id: UUID) -> list[InboundTurn]:
        turns: list[InboundTurn] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    turns.append(self._one(msg, value, tenant_id))
        return turns

    def _one(self, msg: dict, value: dict, tenant_id: UUID) -> InboundTurn:
        mtype = msg.get("type")
        if mtype == "text":
            modality, text = "text", msg["text"]["body"]
        elif mtype == "interactive":
            modality = "button"
            inter = msg["interactive"]
            text = inter.get("button_reply", inter.get("list_reply", {})).get("title", "")
        elif mtype == "button":
            modality, text = "button", msg["button"]["text"]
        else:
            # images, audio, documents. We record them but do not try to read them in Phase A.
            modality, text = "text", f"[{mtype} attachment]"

        return InboundTurn(
            tenant_id=tenant_id,
            channel="whatsapp",
            channel_thread_id=msg["from"],
            channel_identity=f"+{msg['from']}",
            modality=modality,
            text=text,
            received_at=datetime.fromtimestamp(int(msg["timestamp"]), tz=UTC),
            idempotency_key=msg["id"],
            raw=msg,
        )

    async def render(self, action: OutboundAction, turn: InboundTurn) -> None:
        body: dict = {"messaging_product": "whatsapp", "to": turn.channel_thread_id}

        if action.quick_replies:
            body["type"] = "interactive"
            body["interactive"] = {
                "type": "button",
                "body": {"text": action.text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"qr_{i}", "title": r[:20]}}
                        for i, r in enumerate(action.quick_replies)
                    ]
                },
            }
        else:
            body["type"] = "text"
            body["text"] = {"body": action.text}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GRAPH}/{self.phone_number_id}/messages",
                json=body,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
        if resp.status_code >= 400:
            log.error("whatsapp_send_failed", status=resp.status_code, body=resp.text)
            resp.raise_for_status()
