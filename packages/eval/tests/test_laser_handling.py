"""Task 4: the smallest safe handling of a laser request for the DermaClub demo.

The Minimum Conversation plan assumed a bare "ليزر" reached several laser packages and so already
asked "which one?". The later diagnosis found it did not: against the client's own workbook-derived
diary the bare word resolves to *nothing at all* — the three Primelase rows carry "برايم ليز…"
aliases, not "ليزر" — so it hands off rather than clarifying. That fact is taken as authoritative
here and is **not** fixed by adding aliases, editing the catalogue, or changing resolution; it is
pinned by ``test_bare_laser_is_unresolved_on_current_data`` so a later "fix" that quietly makes it
resolve trips this test and has to be a decision rather than an accident.

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

from apps.api.clinic.catalogue import resolve_service

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


def test_bare_laser_is_unresolved_on_current_data() -> None:
    """Records the diagnosis's authoritative finding: bare "ليزر" reaches no catalogue row.

    This is the fact the demo is built around, not a bug to repair. If it ever starts resolving
    (an alias added, the catalogue edited, resolution loosened) this assertion fails, which is the
    point — such a change is out of Task 4's scope and must be a deliberate decision.
    """
    diary = FixtureDiary.from_path(DIARY)
    match = resolve_service("ليزر", diary.services)
    assert match.found is None
    assert match.ambiguous is False
    assert match.candidates == ()


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
