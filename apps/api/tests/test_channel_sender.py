"""The outbound sender (roadmap A6) — the half of the wire that did not exist.

``ChannelSender`` was a protocol with no implementation for five sessions: the receptionist
composed replies and nothing put them anywhere. These tests are about the three things that can
go wrong once something does — the wrong payload shape, the wrong retry decision, and a process
that cannot send at all — and none of them touch the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from apps.api.channels.config import ChannelCredentials
from apps.api.channels.factory import build_sender
from apps.api.channels.whatsapp import (
    BUTTON_TITLE_LIMIT,
    ChannelSendError,
    SendCredentials,
    WhatsAppSender,
    to_payload,
)
from apps.api.schemas.envelope import InboundTurn, OutboundAction

CREDENTIALS = SendCredentials(
    access_token="wa-token", phone_number_id="pn-1", graph_api_version="v21.0"
)


def _turn() -> InboundTurn:
    return InboundTurn(
        tenant_id=uuid.uuid4(),
        channel="whatsapp",
        channel_thread_id="thread-1",
        channel_identity="+966500000000",
        modality="text",
        text="hello",
        received_at=datetime.now(UTC),
        idempotency_key="wamid.A",
    )


def _recording(status: int | list[int]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A transport that records every request and answers with the given status(es) in order."""
    statuses = [status] if isinstance(status, int) else list(status)
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        code = statuses[min(len(seen) - 1, len(statuses) - 1)]
        return httpx.Response(code, json={"messages": [{"id": "wamid.out"}]})

    return httpx.MockTransport(handle), seen


# ── The payload ────────────────────────────────────────────────────────────────────────────


def test_a_plain_reply_is_a_text_message() -> None:
    payload = to_payload(OutboundAction(kind="say", text="All set!"), "+966500000000")
    assert payload["type"] == "text"
    assert payload["to"] == "+966500000000"
    assert payload["text"]["body"] == "All set!"
    assert payload["messaging_product"] == "whatsapp"


def test_quick_replies_become_interactive_buttons() -> None:
    action = OutboundAction(kind="confirm", text="The 4th?", quick_replies=["Yes", "No"])
    payload = to_payload(action, "+966500000000")

    assert payload["type"] == "interactive"
    buttons = payload["interactive"]["action"]["buttons"]
    assert [b["reply"]["title"] for b in buttons] == ["Yes", "No"]
    assert [b["reply"]["id"] for b in buttons] == ["qr_0", "qr_1"]


def test_the_channels_limits_are_applied_on_the_way_out() -> None:
    """The core may compose six long options; this is where reality is applied (trap #2).

    Both limits are WhatsApp's, both are enforced here, and neither is allowed to raise: a
    receptionist that composed one option too many must not become a crashed reply.
    """
    action = OutboundAction(
        kind="ask",
        text="Which?",
        quick_replies=["a" * 40, "second", "third", "fourth"],
    )
    buttons = to_payload(action, "+966500000000")["interactive"]["action"]["buttons"]

    assert len(buttons) == 3  # QUICK_REPLY_LIMIT
    assert len(buttons[0]["reply"]["title"]) == BUTTON_TITLE_LIMIT


# ── The wire ───────────────────────────────────────────────────────────────────────────────


def test_a_reply_is_posted_to_the_versioned_endpoint_for_our_number() -> None:
    transport, seen = _recording(200)
    sender = WhatsAppSender(CREDENTIALS, client=httpx.Client(transport=transport))

    asyncio.run(sender.send(OutboundAction(kind="say", text="All set!"), _turn()))

    assert len(seen) == 1
    assert seen[0].url.path == "/v21.0/pn-1/messages"
    assert seen[0].headers["authorization"] == "Bearer wa-token"


def test_a_rate_limit_is_retried_and_then_succeeds() -> None:
    transport, seen = _recording([429, 200])
    sender = WhatsAppSender(
        CREDENTIALS, client=httpx.Client(transport=transport), sleep=lambda _s: None
    )

    asyncio.run(sender.send(OutboundAction(kind="say", text="hi"), _turn()))

    assert len(seen) == 2  # came back once, as asked


def test_a_rejected_request_is_not_retried() -> None:
    """A revoked token or an unmessageable number fails the same way three times.

    Retrying spends the timeout budget over and over and ends in the same place, so the only
    thing it buys is a slower failure and three times the log noise.
    """
    transport, seen = _recording(401)
    sender = WhatsAppSender(
        CREDENTIALS, client=httpx.Client(transport=transport), sleep=lambda _s: None
    )

    with pytest.raises(ChannelSendError, match="401"):
        asyncio.run(sender.send(OutboundAction(kind="say", text="hi"), _turn()))

    assert len(seen) == 1


def test_a_persistently_failing_send_gives_up_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    transport, seen = _recording(503)
    sender = WhatsAppSender(
        CREDENTIALS,
        client=httpx.Client(transport=transport),
        sleep=lambda _s: None,
        max_attempts=3,
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ChannelSendError, match="undelivered after 3 attempts"):
            asyncio.run(sender.send(OutboundAction(kind="say", text="hi"), _turn()))

    assert len(seen) == 3
    assert "send failed" in caplog.text


def test_the_reply_goes_back_to_whoever_sent_the_message() -> None:
    """The one field that must never be guessed: who the reply is addressed to."""
    transport, seen = _recording(200)
    sender = WhatsAppSender(CREDENTIALS, client=httpx.Client(transport=transport))

    asyncio.run(sender.send(OutboundAction(kind="say", text="hi"), _turn()))

    assert json.loads(seen[0].content)["to"] == "+966500000000"


# ── Choosing a sender at all ───────────────────────────────────────────────────────────────


def test_no_credentials_means_no_sender_rather_than_no_process(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A process that cannot reply still ingests, classifies and files. It says so, loudly."""
    with caplog.at_level(logging.WARNING):
        sender = build_sender(ChannelCredentials(_env_file=None))

    assert sender is None
    assert "no send credentials configured" in caplog.text


def test_credentials_produce_a_usable_sender() -> None:
    credentials = ChannelCredentials(
        _env_file=None,
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="pn-1",
    )
    sender = build_sender(credentials)

    assert isinstance(sender, WhatsAppSender)
    assert sender.url.endswith("/v21.0/pn-1/messages")
    sender.close()
