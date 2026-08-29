"""Regression (Task 3): a real availability offer is the start of a booking, not the end of one.

The flow this pins is the one the diagnosis and the Task 2 summary left open. A patient asks what
is free for a service at a branch on a day — all three slots in one message — and the diary offers a
real time. On the demo script the patient then replies with *just that time* ("الساعة ٧"), which out
of context is two words: the classifier labels it ``unclear`` and it carries no service, branch or
date of its own.

Before Task 3 the successful ``availability_check`` completed, and a completed task leaves the
active set (both ``db/orchestration_repo.py``'s ``get_active_task`` and this harness's
``_Continuity`` key continuity off status). So turn 2 began from an empty slate and handed off —
the context loss this regression exists to catch. The receptionist now continues the *same* task
as a pending
``booking_enquiry`` holding the service, branch and date the availability check collected, so the
bare time is read into ``requested_time`` and flows into the booking read-back.

The proof is entirely in the second turn re-supplying nothing: its label is ``unclear`` with **no**
slots, so reaching the ``19:00`` read-back is only possible if the offered availability survived
as a booking the patient could complete. The third turn confirms it, writing a real ``DC-####``
booking against the client's own diary fixture.

No API key: the labels below are run through the real receptionist and the real tools against the
workbook-derived diary, exactly as the other journey regressions are.
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

AVAILABILITY_THEN_TIME = JourneyCase(
    id="availability_offer_then_bare_time",
    title="ask what's free (service+branch+date) → real offer → a bare time → booking read-back",
    tags=("booking", "ar", "regression", "continuity"),
    turns=(
        JourneyTurn(
            # All three slots in one message, so the availability check runs and offers a real time
            # rather than asking for the service (which is the Task 2 flow, pinned elsewhere).
            message="في ميعاد فاشيال في المعادي بكرة؟",
            label=TurnLabel(
                intent="availability_check",
                confidence=0.94,
                slots={
                    "service": "فاشيال",
                    "branch": "المعادي",
                    "requested_date": "2026-09-02",
                },
            ),
            # A concrete offer that is now kept alive as a pending booking (``collecting``) rather
            # than completing and dropping its context before the patient can pick a time.
            expect=TurnExpectation(
                kind="say",
                includes=("19:00",),
                task_status="collecting",
            ),
        ),
        JourneyTurn(
            # The bare offered time. ``unclear`` with no slots: it re-supplies neither service,
            # branch nor date. Reaching the read-back is the proof the offered availability carried
            # them forward.
            message="الساعة ٧",
            label=TurnLabel(intent="unclear", confidence=0.3, slots={}),
            expect=TurnExpectation(
                kind="confirm",
                includes=("19:00", "Wednesday 02 September"),
                # Neither the English slot prompt nor a hand-off: the two failures this flow used to
                # end in.
                excludes=("Could you please provide", "connect you with someone"),
            ),
        ),
        JourneyTurn(
            # And it finishes: "تمام" agrees to the read-back and a real appointment is written.
            message="تمام",
            label=TurnLabel(intent="thanks_closing", confidence=0.95, slots={}),
            expect=TurnExpectation(
                kind="say",
                includes=("DC-0266",),
                bookings=1,
                task_status="completed",
            ),
        ),
    ),
)


def test_a_real_offer_continues_into_the_booking_read_back() -> None:
    diary = FixtureDiary.from_path(DIARY)
    outcome = run_journey(AVAILABILITY_THEN_TIME, diary, vocabulary=CLINICS)
    assert outcome.ok, "first failing turn: " + repr(outcome.first_failure)
    assert outcome.bookings == ("DC-0266",)
