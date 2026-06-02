"""Parse a Meta WhatsApp Cloud API webhook payload into normalized message envelopes.

Only ``messages.*`` entries become envelopes; ``statuses.*`` (delivered/read) are dropped (addendum
§5). Parsing is defensive — a malformed entry is skipped rather than failing the batch, so one bad
message never blocks the rest (Meta sends batches).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from apps.api.schemas.enums import MessageDirection, MessageType, SourceKind
from apps.api.schemas.message import MessageEnvelope

# Meta message ``type`` → our MessageType. Anything else (video, sticker, location, …) → OTHER.
_TYPE_MAP: dict[str, MessageType] = {
    "text": MessageType.TEXT,
    "audio": MessageType.AUDIO,
    "image": MessageType.IMAGE,
    "document": MessageType.DOCUMENT,
}


def _to_e164(wa_id: str) -> str:
    """Meta sends ``wa_id`` as bare digits; render it E.164 with a leading ``+``."""
    return wa_id if wa_id.startswith("+") else f"+{wa_id}"


def _names_by_wa_id(value: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for contact in value.get("contacts", []):
        wa_id = contact.get("wa_id")
        name = contact.get("profile", {}).get("name")
        if isinstance(wa_id, str) and isinstance(name, str):
            names[wa_id] = name
    return names


def _parse_message(raw: dict[str, Any], names: dict[str, str]) -> MessageEnvelope | None:
    sender = raw.get("from")
    wa_message_id = raw.get("id")
    timestamp = raw.get("timestamp")
    meta_type = raw.get("type")
    if not (isinstance(sender, str) and isinstance(wa_message_id, str) and timestamp is not None):
        return None

    msg_type = (
        _TYPE_MAP.get(meta_type, MessageType.OTHER)
        if isinstance(meta_type, str)
        else MessageType.OTHER
    )

    body_text: str | None = None
    media_id: str | None = None
    media_mime: str | None = None
    if msg_type is MessageType.TEXT:
        body_text = raw.get("text", {}).get("body")
    elif isinstance(meta_type, str) and isinstance(raw.get(meta_type), dict):
        media = raw[meta_type]
        media_id = media.get("id")
        media_mime = media.get("mime_type")
        body_text = media.get("caption")  # images/documents may carry a caption

    return MessageEnvelope(
        wa_message_id=wa_message_id,
        wa_chat_id=sender,  # Cloud API delivers 1:1 chats; group support is its own spec (§17.12)
        source_kind=SourceKind.DIRECT,
        sender_phone_e164=_to_e164(sender),
        sender_wa_name=names.get(sender),
        direction=MessageDirection.INBOUND,
        type=msg_type,
        body_text=body_text,
        media_id=media_id,
        media_mime=media_mime,
        received_at=datetime.fromtimestamp(int(timestamp), tz=UTC),
        raw_payload=raw,
    )


def iter_change_values(payload: dict[str, Any]) -> Iterator[tuple[str | None, dict[str, Any]]]:
    """Yield ``(phone_number_id, value)`` per change so callers can resolve the tenant (§3)."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            yield (phone_number_id if isinstance(phone_number_id, str) else None, value)


def parse_value(value: dict[str, Any]) -> list[MessageEnvelope]:
    """Extract inbound message envelopes from a single change ``value`` (statuses ignored)."""
    names = _names_by_wa_id(value)
    envelopes: list[MessageEnvelope] = []
    for raw in value.get("messages", []):
        if not isinstance(raw, dict):
            continue
        envelope = _parse_message(raw, names)
        if envelope is not None:
            envelopes.append(envelope)
    return envelopes


def parse_webhook(payload: dict[str, Any]) -> list[MessageEnvelope]:
    """Extract all inbound message envelopes from a Meta webhook payload (statuses ignored)."""
    envelopes: list[MessageEnvelope] = []
    for _phone_number_id, value in iter_change_values(payload):
        envelopes.extend(parse_value(value))
    return envelopes
