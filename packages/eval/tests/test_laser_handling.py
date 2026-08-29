"""Task 4: the smallest safe handling of a laser request for the DermaClub demo.

The authoritative behaviour of a bare "ليزر" belongs to the full DermaClub workbook, where the word
is *ambiguous* across the clinic's laser services and must never silently choose one. That invariant
is pinned at the authoritative-data level, against the workbook itself, in
``apps/api/tests/test_clinic_workbook_integration.py`` — not here. The reduced eval diary in
``packages/eval/fixtures/clinic_diary.json`` is only a two-branch, one-day *cut* of that workbook,
and happens to carry none of those laser rows, so a claim about what bare "ليزر" does against the
cut is an accident of the subset, not a contract: regenerating the fixture from a fuller slice could
change it without any production behaviour changing. So this file makes no assertion about the bare
word.

What Task 4 does change is one thing: the genuinely ambiguous laser path — bare "برايم ليز", which
*is* three packages — now asks its "which one?" in Egyptian Arabic through the existing
``ConversationCopy.choose_one`` / ``TENANT_CHOOSE_ONE`` seam, instead of the English constant it
used to fall back to. And the demo's bookable laser is a *concrete* package ("برايم ليز جلسة واحدة",
Primelase single session), which resolves to exactly one row and runs the existing availability →
read-back → booking journey to a real reference.

No API key: the labels are run through the real receptionist and the real tools against the client
diary, exactly as the other journey regressions are.
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


#: A genuinely ambiguous laser request: bare "برايم ليز" is the single session, the six-session
#: package and the twelve-session one at once.
AMBIGUOUS_LASER = JourneyCase(
    id="ambiguous_laser_asks_which_one_in_arabic",
    title="bare برايم ليز → the Egyptian-Arabic 'which one?' question, not the English fallback",
    tags=("laser", "ar", "clarify", "task4"),
    turns=(
        JourneyTurn(
            message="في ميعاد برايم ليز في المعادي بكرة؟",
            label=TurnLabel(
                intent="availability_check",
                confidence=0.94,
                slots={
                    "service": "برايم ليز",
                    "branch": "المعادي",
                    "requested_date": "2026-09-02",
                },
            ),
            expect=TurnExpectation(
                kind="ask",
                # The Arabic clarifying frame from ``choose_one``'s in-code default …
                includes=("تحبي أنهي واحدة فيهم",),
                # … in place of the English constant it used to fall back to.
                excludes=("Which did you mean",),
            ),
        ),
    ),
)


def test_an_ambiguous_laser_request_asks_the_arabic_which_one() -> None:
    diary = FixtureDiary.from_path(DIARY)
    outcome = run_journey(AMBIGUOUS_LASER, diary, vocabulary=CLINICS)
    assert outcome.ok, "first failing turn: " + repr(outcome.first_failure)


#: The demo's bookable laser: a *concrete* package that resolves to exactly one catalogue row and
#: has a real free slot in the diary (Primelase single session, S00225, 11:00 at Maadi).
CONCRETE_LASER = JourneyCase(
    id="concrete_laser_books_end_to_end",
    title="برايم ليز جلسة واحدة → real 11:00 offer → bare time → read-back → a DC-#### booking",
    tags=("laser", "ar", "booking", "task4"),
    turns=(
        JourneyTurn(
            message="في ميعاد برايم ليز جلسة واحدة في المعادي بكرة؟",
            label=TurnLabel(
                intent="availability_check",
                confidence=0.94,
                slots={
                    "service": "برايم ليز جلسة واحدة",
                    "branch": "المعادي",
                    "requested_date": "2026-09-02",
                },
            ),
            # One row, one offer: no "which one?" here — the concrete package resolves to exactly
            # one row and the diary offers its real 11:00 slot, kept alive as a pending booking.
            expect=TurnExpectation(
                kind="say",
                includes=("11:00",),
                task_status="collecting",
            ),
        ),
        JourneyTurn(
            # The bare offered time, out of context: ``unclear`` with no slots. Reaching the
            # read-back is the proof the concrete offer carried service, branch and date forward.
            message="الساعة ١١",
            label=TurnLabel(intent="unclear", confidence=0.3, slots={}),
            expect=TurnExpectation(
                kind="confirm",
                includes=("11:00", "Wednesday 02 September"),
                excludes=("Could you please provide", "connect you with someone"),
            ),
        ),
        JourneyTurn(
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


def test_the_concrete_laser_package_books_against_the_real_diary() -> None:
    diary = FixtureDiary.from_path(DIARY)
    outcome = run_journey(CONCRETE_LASER, diary, vocabulary=CLINICS)
    assert outcome.ok, "first failing turn: " + repr(outcome.first_failure)
    assert outcome.bookings == ("DC-0266",)
