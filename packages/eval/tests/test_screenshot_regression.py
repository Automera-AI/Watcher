"""Regression: the WhatsApp screenshot flow, pinned against the current receptionist.

The screenshot showed a patient greeted correctly and then met with the same English
fallback on every following message. With the four clinic tools registered (the confirmed
live state), that specific fallback (``_UNBUILT_TEXT``) is unreachable for a WhatsApp patient
— it only appears when the clinic tools are *absent*. What the deployed code actually does on
this flow is different and is what this test pins.

The labels below are the ones the live classifier produced for these exact messages
(``claude-haiku-4-5``, clinics prompt v5, tenant clock 2026-09-01 Africa/Cairo), captured
during diagnosis. The test needs no API key: it runs those recorded labels through the real
receptionist and the real tools against the client's own diary fixture.

**The turn that fails is turn 1.** A patient who gives a branch and a day but no service
(``…في المعادي بكرة ايه المتاح؟``) is asked for the service in a hardcoded English sentence —
``apps/api/conversations/receptionist.py`` ``handle``'s ``"Could you please provide the
{slot}?"`` — instead of a contextual Arabic question. Turn 2, with a service that resolves
(``فاشيال`` → ``Facial``), already reaches a real workbook-backed offer, so the machinery
downstream of the ask works; the defect is the ask itself.

Marked ``xfail(strict=True)`` deliberately, the same way the journey set carries a declared
gap: it documents the intended behaviour, fails today, and the strict marker turns an
unexpected pass into a failure — so when the Arabic contextual ask lands, this marker has to
come off. Do not make it pass by weakening the assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.eval.journeys import (
    FixtureDiary,
    JourneyCase,
    JourneyTurn,
    TurnExpectation,
    TurnLabel,
    run_journey,
)
from packages.intents.schema import vocabulary_for

ROOT = Path(__file__).resolve().parents[1]
DIARY = ROOT / "fixtures/clinic_diary.json"
CLINICS = vocabulary_for("clinics")

# What the live classifier actually returned for these messages, captured during diagnosis.
SCREENSHOT = JourneyCase(
    id="screenshot_availability_then_service",
    title="greeting → book with branch+date but no service → concrete service answer",
    tags=("booking", "ar", "regression"),
    turns=(
        JourneyTurn(
            message="مساء الخير",
            label=TurnLabel(intent="greeting", confidence=0.98, slots={}),
            expect=TurnExpectation(kind="say"),
        ),
        JourneyTurn(
            message="عايزة احجز بكرة في المعادي ايه المتاح؟",
            label=TurnLabel(
                intent="availability_check",
                confidence=0.94,
                slots={"branch": "المعادي", "requested_date": "2026-09-02"},
            ),
            # Intended: an Arabic question that uses the branch and day it already has. The
            # defect is that this comes back as the English "Could you please provide the
            # service?" — so the assertion the flow fails on today is this exclusion.
            expect=TurnExpectation(kind="ask", excludes=("Could you please provide",)),
        ),
        JourneyTurn(
            message="فاشيال",
            label=TurnLabel(
                intent="booking_enquiry",
                confidence=0.95,
                slots={
                    "service": "فاشيال",
                    "branch": "المعادي",
                    "requested_date": "2026-09-02",
                },
            ),
            # A service that resolves reaches the real, workbook-backed offer already.
            expect=TurnExpectation(kind="ask", includes=("19:00",)),
        ),
    ),
)


@pytest.mark.xfail(
    strict=True,
    reason="receptionist asks for a missing service in hardcoded English "
    "(receptionist.py handle: 'Could you please provide the {slot}?'); "
    "when the contextual Arabic ask lands, remove this marker",
)
def test_the_screenshot_flow_answers_the_missing_service_in_arabic() -> None:
    diary = FixtureDiary.from_path(DIARY)
    outcome = run_journey(SCREENSHOT, diary, vocabulary=CLINICS)
    assert outcome.ok, "first failing turn: " + repr(outcome.first_failure)
