"""The emergency alert path (roadmap G3, the half that reaches a person).

Detection is tested in ``test_emergency.py`` and the orchestration of it in
``test_orchestration.py``. What is left, and what is here, is the delivery: the alert goes to the
*operator's* number rather than the guest's, it carries enough for a person to act without opening
anything, it never raises into the message path, and — the assertion that keeps this honest — it
reports the channel it actually used rather than the one ``intents.yaml`` asked for.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
from packages.intents.schema import default_vocabulary

from apps.api.channels.alerting import WHATSAPP_TEXT, WhatsAppOperatorAlerter
from apps.api.channels.factory import build_alerter
from apps.api.channels.whatsapp import SendCredentials, WhatsAppSender
from apps.api.core.alerts import LOG_ONLY, AlertOutcome, EmergencyAlert

OPERATOR = "+971500000001"
GUEST = "+966500000000"
DECLARED = default_vocabulary().emergency.alert

CREDENTIALS = SendCredentials(
    access_token="wa-token", phone_number_id="pn-1", graph_api_version="v21.0"
)


def _alert() -> EmergencyAlert:
    return EmergencyAlert(
        tenant_id="11111111-1111-4111-8111-111111111111",
        message_id="wamid.A",
        trigger_id="gas",
        matched="smell of gas",
        guest_identity=GUEST,
        thread_id="thread-1",
        text="there is a smell of gas in the kitchen",
        received_at=datetime(2026, 8, 16, 22, 30, tzinfo=UTC),
        requested_channel=DECLARED,
    )


def _transport(status: int) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"messages": [{"id": "wamid.out"}]})

    return httpx.MockTransport(handle), seen


def _alerter(status: int = 200) -> tuple[WhatsAppOperatorAlerter, list[httpx.Request]]:
    transport, seen = _transport(status)
    sender = WhatsAppSender(
        CREDENTIALS,
        client=httpx.Client(transport=transport),
        max_attempts=1,
        sleep=lambda _s: None,
    )
    return WhatsAppOperatorAlerter(sender, OPERATOR), seen


# ── What the operator gets ─────────────────────────────────────────────────────────────────


def test_the_alert_goes_to_the_operator_and_not_to_the_guest() -> None:
    """The one field that must not be copied from the turn that caused it."""
    alerter, seen = _alerter()
    outcome = asyncio.run(alerter.alert(_alert()))

    assert outcome.delivered is True
    body = json.loads(seen[0].content)
    assert body["to"] == OPERATOR
    assert GUEST in body["text"]["body"]  # the guest's number is *in* the alert, not its address


def test_the_alert_carries_what_the_guest_said() -> None:
    """An alert that withholds the sentence makes the operator go and look it up."""
    summary = _alert().summary()
    assert "EMERGENCY" in summary
    assert "gas" in summary
    assert "there is a smell of gas in the kitchen" in summary
    assert GUEST in summary
    assert "wamid.A" in summary


def test_a_media_message_with_no_text_still_produces_a_usable_alert() -> None:
    """A voice note nothing could transcribe is still an emergency someone must be told about."""
    summary = EmergencyAlert(
        tenant_id="t",
        message_id="wamid.B",
        trigger_id="fire",
        matched="fire",
        guest_identity=GUEST,
        thread_id="thread-1",
        text=None,
        received_at=datetime(2026, 8, 16, 22, 30, tzinfo=UTC),
        requested_channel=DECLARED,
    ).summary()
    assert "no text" in summary


# ── Failure, and the gap that is left open on purpose ──────────────────────────────────────


def test_an_undeliverable_alert_is_reported_and_logged_rather_than_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Raising here would cost the guest the immediate reply the vocabulary requires."""
    alerter, _seen = _alerter(status=401)

    with caplog.at_level(logging.CRITICAL):
        outcome = asyncio.run(alerter.alert(_alert()))

    assert outcome.delivered is False
    assert "EMERGENCY ALERT UNDELIVERED" in caplog.text


def test_a_delivered_message_does_not_claim_to_be_the_declared_phone_call() -> None:
    """The vocabulary asks for a call; this sends a message. The difference stays visible."""
    alerter, _seen = _alerter()
    outcome = asyncio.run(alerter.alert(_alert()))

    assert outcome.channel == WHATSAPP_TEXT
    assert DECLARED == "phone_call_to_operator"
    assert outcome.satisfies(DECLARED) is False


def test_an_outcome_only_satisfies_the_channel_it_was_delivered_on() -> None:
    assert AlertOutcome(delivered=True, channel=DECLARED).satisfies(DECLARED) is True
    assert AlertOutcome(delivered=False, channel=DECLARED).satisfies(DECLARED) is False
    assert AlertOutcome(delivered=True, channel=LOG_ONLY).satisfies(DECLARED) is False


# ── Choosing an alerter at all ─────────────────────────────────────────────────────────────


def test_no_operator_number_means_no_alerter_and_a_loud_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The most serious degraded state this process has, and it is announced as one."""
    transport, _seen = _transport(200)
    sender = WhatsAppSender(CREDENTIALS, client=httpx.Client(transport=transport))

    with caplog.at_level(logging.WARNING):
        alerter = build_alerter(sender, None, declared_channel=DECLARED)

    assert alerter is None
    assert "no emergency alert path configured" in caplog.text
    sender.close()


def test_no_sender_means_no_alerter() -> None:
    """Without send credentials there is no way out of the process at all."""
    assert build_alerter(None, OPERATOR, declared_channel=DECLARED) is None


def test_the_declared_channel_being_unreachable_is_warned_about_once_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport, _seen = _transport(200)
    sender = WhatsAppSender(CREDENTIALS, client=httpx.Client(transport=transport))

    with caplog.at_level(logging.WARNING):
        alerter = build_alerter(sender, OPERATOR, declared_channel=DECLARED)

    assert isinstance(alerter, WhatsAppOperatorAlerter)
    assert "phone_call_to_operator" in caplog.text
    sender.close()
