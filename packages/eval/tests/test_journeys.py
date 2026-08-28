"""Journey eval tests: the committed set, the diary, and the harness's own honesty (step 9).

Two different things are under test here and they are worth keeping apart.

The first is the **committed journey set**, which is a statement about the demo: the booking
conversation the client will run works against the diary the client exported, the safety journeys
stop where they must, and the one declared gap is still a gap. If those break, something about the
demo broke.

The second is the **harness**, and the property that matters is that it can fail. An eval that
reports success whatever the system does is worse than no eval, so the tests below deliberately
break a journey — a wrong expectation, a slot taken underneath it, a message the recording does not
cover — and check the failure is reported, at the right turn, with a reason a person can read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from apps.api.schemas.classification import ClassificationResult

from packages.eval.cases import load_fixtures
from packages.eval.cli import main
from packages.eval.journeys import (
    FixtureDiary,
    JourneyCase,
    JourneyTurn,
    TurnExpectation,
    TurnLabel,
    load_journeys,
    run_journey,
    run_journeys,
)
from packages.intents.schema import vocabulary_for

ROOT = Path(__file__).resolve().parents[1]
JOURNEYS = ROOT / "golden/clinics_journeys.jsonl"
DIARY = ROOT / "fixtures/clinic_diary.json"
LIVE_LABELS = ROOT / "fixtures/recorded_clinics_journey_haiku.jsonl"

CLINICS = vocabulary_for("clinics")

#: The one open facial slot at Maadi on the demo's Wednesday, and its time. Named here because
#: several tests depend on the diary holding exactly one, which is the fact the fixture exists to
#: carry — see ``test_the_diary_holds_one_slot_per_service_branch_and_day``.
FACIAL_SLOT = "S00232"
FACIAL_TIME = "19:00"


@pytest.fixture
def diary() -> FixtureDiary:
    return FixtureDiary.from_path(DIARY)


# ── The committed set ──────────────────────────────────────────────────────────────────────


def test_every_journey_in_the_set_passes_against_the_clients_own_diary(
    diary: FixtureDiary,
) -> None:
    """The demo, measured. A failure here is a failure the client would see on the day."""
    report = run_journeys(load_journeys(JOURNEYS), diary, vocabulary=CLINICS)

    assert report.total >= 8, "the set has shrunk — journeys were removed rather than fixed"
    failed = [
        outcome.case.id
        for outcome in report.outcomes
        if not outcome.ok and not outcome.case.known_gap
    ]
    assert not failed, f"journeys broke: {failed}"
    assert report.turn_accuracy == 1.0


def test_every_journey_survives_what_the_model_actually_says(diary: FixtureDiary) -> None:
    """The same set, on recorded classifications rather than the labels we wrote for it.

    This is the run that matters, and the first time it was made it scored 5 of 9. The written
    labels say `booking_enquiry` for "الساعة ٧"; the model says `unclear`, at 0.25 on the cheap
    tier and 0.3 after escalating, because out of context two words naming an hour are not a
    booking request. That switched tasks and fetched a person one turn after the patient had been
    offered a time — the demo's own second turn.

    Two fixes came out of it and both are load-bearing here: an `unclear` turn is offered to the
    slot the task is waiting on, and an intent the conversation supplied is not gated by how sure
    the model was about a label it did not choose. If this test fails and the written-label one
    passes, the difference is the classifier, not the conversation.
    """
    labels = {
        message: TurnLabel.from_result(result)
        for message, result in load_fixtures(LIVE_LABELS).items()
        if result is not None
    }
    report = run_journeys(load_journeys(JOURNEYS), diary, vocabulary=CLINICS, labels=labels)

    broken = [outcome.case.id for outcome in report.gated if not outcome.ok]
    assert not broken, f"journeys broke on the model's own labels: {broken}"
    assert report.turn_accuracy == 1.0


def test_the_declared_gap_is_still_a_gap(diary: FixtureDiary) -> None:
    """A known gap that starts passing is news, and the report says so rather than hiding it.

    The gap itself: the booking reference lives on the task, and the task is finished by the time
    the patient says thank you, so the confirmed-booking closing renders its generic form. When
    that is fixed this test fails, which is the point — the flag comes off the journey and the
    journey joins the gate.
    """
    report = run_journeys(load_journeys(JOURNEYS), diary, vocabulary=CLINICS)

    assert [outcome.case.id for outcome in report.known_gaps] == [
        "the_closing_quotes_the_reference"
    ]
    assert not report.closed_gaps, (
        "a journey marked known_gap now passes: "
        f"{[outcome.case.id for outcome in report.closed_gaps]} — drop the flag"
    )
    # And it does not drag the gate down with it.
    assert report.journey_accuracy == 1.0


def test_the_diary_holds_one_slot_per_service_branch_and_day(diary: FixtureDiary) -> None:
    """The fact that makes the handoff's "11:00 / 16:00 / 18:00" script impossible.

    Every (date, branch, service) in the client's workbook has exactly one slot, open or booked.
    A demo scripted around a choice of three times cannot run on this data, and a journey eval
    against an invented diary would never have said so.
    """
    seen: dict[tuple[str, str], int] = {}
    for slot in diary.slots:
        key = (slot.branch_external_id, slot.service_code)
        seen[key] = seen.get(key, 0) + 1

    assert set(seen.values()) == {1}
    offered = diary.available_slots(
        "t",
        service_code="DT002",
        branch_external_id="DC01",
        on_date=diary.now.date().replace(day=2),
        timezone=diary.timezone,
    )
    assert [slot.external_id for slot in offered] == [FACIAL_SLOT]


# ── The diary behaves like the database it stands in for ───────────────────────────────────


def test_a_hold_is_opaque_to_every_other_conversation(diary: FixtureDiary) -> None:
    assert diary.hold_slot(
        "t", slot_external_id=FACIAL_SLOT, conversation_id="c1", until=diary.now.replace(hour=23)
    )
    assert not diary.hold_slot(
        "t", slot_external_id=FACIAL_SLOT, conversation_id="c2", until=diary.now.replace(hour=23)
    )
    # …and invisible to the conversation that placed it, which still sees its own slot.
    mine = diary.available_slots(
        "t",
        service_code="DT002",
        branch_external_id="DC01",
        on_date=diary.now.date().replace(day=2),
        timezone=diary.timezone,
        conversation_id="c1",
    )
    theirs = diary.available_slots(
        "t",
        service_code="DT002",
        branch_external_id="DC01",
        on_date=diary.now.date().replace(day=2),
        timezone=diary.timezone,
        conversation_id="c2",
    )
    assert [slot.external_id for slot in mine] == [FACIAL_SLOT]
    assert theirs == []


def test_confirming_twice_returns_the_same_appointment(diary: FixtureDiary) -> None:
    first = diary.confirm_booking(
        "t", slot_external_id=FACIAL_SLOT, conversation_id="c1", reference_prefix="DC"
    )
    second = diary.confirm_booking(
        "t", slot_external_id=FACIAL_SLOT, conversation_id="c1", reference_prefix="DC"
    )
    assert first.reason == "confirmed"
    assert second.reason == "already_confirmed"
    assert second.booking is not None and second.booking.reference == "DC-0266"
    assert len(diary.bookings) == 1


def test_a_slot_somebody_else_took_cannot_be_confirmed(diary: FixtureDiary) -> None:
    diary.take(FACIAL_SLOT)
    outcome = diary.confirm_booking(
        "t", slot_external_id=FACIAL_SLOT, conversation_id="c1", reference_prefix="DC"
    )
    assert outcome.reason == "slot_taken"
    assert (
        diary.confirm_booking(
            "t", slot_external_id="nope", conversation_id="c1", reference_prefix="DC"
        ).reason
        == "slot_unknown"
    )


def test_each_journey_starts_from_the_diary_as_exported(diary: FixtureDiary) -> None:
    """``fresh`` is what stops one journey's booking leaking into the next one's availability."""
    diary.confirm_booking(
        "t", slot_external_id=FACIAL_SLOT, conversation_id="c1", reference_prefix="DC"
    )
    again = diary.fresh()
    assert again.bookings == []
    assert [slot.status for slot in again.slots if slot.external_id == FACIAL_SLOT] == ["open"]


# ── The harness can fail ───────────────────────────────────────────────────────────────────


def _booking_turn(expect: TurnExpectation) -> JourneyTurn:
    return JourneyTurn(
        message="عايزة أحجز فاشيال في المعادي بكرة",
        label=TurnLabel(
            intent="booking_enquiry",
            slots={"service": "فاشيال", "branch": "المعادي", "requested_date": "2026-09-02"},
        ),
        expect=expect,
    )


def test_a_wrong_expectation_is_reported_on_the_turn_that_broke(diary: FixtureDiary) -> None:
    case = JourneyCase(
        id="wrong",
        title="expects a time the diary does not hold",
        turns=(
            _booking_turn(TurnExpectation(kind="say", includes=("11:00",), excludes=("19:00",))),
        ),
    )
    outcome = run_journey(case, diary, vocabulary=CLINICS)

    assert not outcome.ok
    failure = outcome.first_failure
    assert failure is not None and failure.index == 0
    assert failure.failures == (
        "expected a say, got a ask",
        "missing '11:00'",
        "said '19:00', which it must never say here",
    )


def test_a_journey_stops_at_the_first_turn_the_recording_does_not_cover(
    diary: FixtureDiary,
) -> None:
    """Running on recorded labels must not quietly fall back to the written ones.

    The whole reason to run that way is to find out what the model actually said. A message the
    recording does not cover is an answer nobody has — not a licence to use the expected label.
    """
    case = JourneyCase(id="x", title="x", turns=(_booking_turn(TurnExpectation(kind="ask")),))
    outcome = run_journey(case, diary, vocabulary=CLINICS, labels={})

    assert not outcome.ok
    assert outcome.turns[0].failures == ("no recorded classification for this message",)


def test_recorded_labels_replace_the_written_ones(diary: FixtureDiary) -> None:
    """And when the recording *is* there, it is what the journey runs on."""
    case = JourneyCase(
        id="x",
        title="x",
        turns=(_booking_turn(TurnExpectation(kind="handoff")),),
    )
    recorded = TurnLabel(intent="clinical_question", confidence=0.99, slots={})
    outcome = run_journey(
        case,
        diary,
        vocabulary=CLINICS,
        labels={"عايزة أحجز فاشيال في المعادي بكرة": recorded},
    )
    assert outcome.ok, outcome.turns[0].failures


def test_a_label_is_read_off_a_recorded_classification() -> None:
    result = ClassificationResult.model_validate(
        {
            "intent": "booking_enquiry",
            "summary_one_line": "x",
            "language": "ar",
            "confidence_overall": 0.9,
            "confidence_intent": 0.91,
            "confidence_person": 0.1,
            "confidence_company": 0.1,
            "extracted_slots": {"service": "فاشيال"},
        }
    )
    label = TurnLabel.from_result(result)
    assert (label.intent, label.confidence, label.slots) == (
        "booking_enquiry",
        0.91,
        {"service": "فاشيال"},
    )


def test_the_registry_is_handed_back_afterwards(diary: FixtureDiary) -> None:
    """A journey borrows the process-global tool registry; it must not keep it."""
    from apps.api.conversations.tools import REGISTRY

    before = dict(REGISTRY)
    run_journey(
        JourneyCase(id="x", title="x", turns=(_booking_turn(TurnExpectation()),)),
        diary,
        vocabulary=CLINICS,
    )
    assert dict(REGISTRY) == before


def test_the_report_serialises_every_turn_and_its_failures(diary: FixtureDiary) -> None:
    report = run_journeys(load_journeys(JOURNEYS), diary, vocabulary=CLINICS, diary_name="d.json")
    document = report.as_dict()

    assert document["diary"] == "d.json"
    assert document["label_source"] == "the journey file's own labels"
    assert document["known_gaps"] == ["the_closing_quotes_the_reference"]
    booking = next(r for r in document["results"] if r["id"] == "booking_facial_maadi")
    assert [turn["kind"] for turn in booking["turns"]] == ["ask", "confirm", "say"]
    assert booking["bookings"] == ["DC-0266"]
    assert json.dumps(document)  # the CLI writes this out, so it has to be serialisable


def test_an_empty_journey_file_is_an_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no journeys"):
        load_journeys(empty)


# ── The command line ───────────────────────────────────────────────────────────────────────


def test_the_cli_runs_the_journeys_and_writes_the_report(tmp_path: Path) -> None:
    code = main(
        [
            "--journeys",
            str(JOURNEYS),
            "--diary",
            str(DIARY),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 0
    written = json.loads((tmp_path / "journeys.json").read_text(encoding="utf-8"))
    assert written["journey_accuracy"] == 1.0
    assert written["turns"] == written["turns_passed"]


def test_the_cli_fails_the_build_when_a_journey_breaks(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    broken = tmp_path / "broken.jsonl"
    broken.write_text(
        json.dumps(
            {
                "id": "broken",
                "title": "expects an appointment nobody made",
                "turns": [
                    {
                        "message": "تمام",
                        "label": {"intent": "thanks_closing", "slots": {}},
                        "expect": {"kind": "say", "includes": ["DC-0266"], "bookings": 1},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert main(["--journeys", str(broken), "--diary", str(DIARY)]) == 1
    printed = capsys.readouterr().out
    assert "journey eval FAILED" in printed
    assert "missing 'DC-0266'" in printed


def test_the_cli_will_not_run_journeys_without_a_diary() -> None:
    with pytest.raises(SystemExit, match="needs --diary"):
        main(["--journeys", str(JOURNEYS)])


def test_the_cli_asks_for_exactly_one_eval() -> None:
    with pytest.raises(SystemExit, match="exactly one"):
        main([])
    with pytest.raises(SystemExit, match="exactly one"):
        main(["--golden", "x.jsonl", "--journeys", str(JOURNEYS)])


def test_the_classifier_eval_still_needs_its_fixtures() -> None:
    with pytest.raises(SystemExit, match="needs --fixtures"):
        main(["--golden", "x.jsonl"])
