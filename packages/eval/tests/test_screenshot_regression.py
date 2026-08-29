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
{slot}?"`` — instead of a contextual Arabic question. The contextual Arabic ask has landed
(``receptionist.py`` ``_ask_for_service``), so this now passes: turn 1 comes back as the Arabic
question that carries the branch and day the task already holds.

**What the assertions pin, and why they were tightened.** Turn 1 is asserted *exactly* —
``أكيد، تحبي تحجزي أنهي خدمة في فرع المعادي بكرة؟`` — not merely for the absence of the English
string, so that an unrelated replacement cannot pass. And turn 2 (``فاشيال``) now supplies **only**
the service: the branch and day are gone from its label, so the ``19:00`` offer can only be reached
if they survived in the task from turn 1. That makes the offer a proof that context carried across
the turn, which a label re-supplying branch and date could not be.

The flow runs under the labels the live classifier actually produced: turn 1 is
``availability_check`` (asking what is free) and turn 2 is ``booking_enquiry`` (naming the
treatment). That intent change used to reset the task and drop the branch and day — the context
loss this regression is about. The receptionist now continues the task across the one compatible
transition ``availability_check`` → ``booking_enquiry`` (``receptionist.py``
``_COMPATIBLE_TRANSITIONS``), keeping the branch and day the availability check already collected,
so the booking that continues it is only missing the time it offers. Reaching the real,
workbook-backed ``19:00`` on turn 2 with only the service supplied is the proof that it did.
"""

from __future__ import annotations

from pathlib import Path

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
            # An Arabic question that uses the branch and day the task already holds. The exact
            # wording is pinned in the test body (below); the exclusion here is a second guard that
            # the old English slot prompt is gone.
            expect=TurnExpectation(kind="ask", excludes=("Could you please provide",)),
        ),
        JourneyTurn(
            message="فاشيال",
            label=TurnLabel(
                intent="booking_enquiry",
                confidence=0.95,
                # Only the service. Branch and day are deliberately absent: for the 19:00 offer to
                # be reached they must survive in the task from the previous turn, which is the
                # context-carrying this regression exists to prove.
                slots={"service": "فاشيال"},
            ),
            # The service resolves and, with the surviving branch and day, reaches the real,
            # workbook-backed offer.
            expect=TurnExpectation(kind="ask", includes=("19:00",)),
        ),
    ),
)


def test_the_screenshot_flow_answers_the_missing_service_in_arabic() -> None:
    diary = FixtureDiary.from_path(DIARY)
    outcome = run_journey(SCREENSHOT, diary, vocabulary=CLINICS)
    assert outcome.ok, "first failing turn: " + repr(outcome.first_failure)
    # The exact contextual Arabic ask — the branch and day come from the task, not this turn's
    # wording — so an unrelated replacement of the sentence cannot slip through.
    assert outcome.turns[1].text == "أكيد، تحبي تحجزي أنهي خدمة في فرع المعادي بكرة؟"
