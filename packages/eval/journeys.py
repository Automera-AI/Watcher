"""Journey evals: the booking conversation, turn by turn, against the client's own diary (step 9).

The classifier eval in this package scores one message at a time. Every failure the booking journey
is actually about happens *between* messages — a detail agreed on one turn and forgotten by the
next, a slot offered and then given away, a "تمام" that ends the conversation instead of finishing
it — and none of those can be seen one message at a time. This module runs whole conversations and
scores what the patient would have received on each turn.

**It is not a second copy of ``test_booking_journey.py``.** That test builds an invented three-slot
diary to pin the receptionist's decisions, which is the right shape for a unit test and the wrong
one for measuring a demo. This runs against ``fixtures/clinic_diary.json``, cut straight out of the
client's workbook, and the first thing it found was that the diary holds **exactly one slot per
service, branch and day** — so the scripted "I can offer 11:00 / 16:00 / 18:00" cannot happen with
this data, and the time the patient is asked to agree to is whichever single slot is open. A test
with an invented diary cannot find that; the whole point of this file is that it can.

**Three seams, and each is deliberate.**

*Where the labels come from.* A journey turn carries the intent and slots the classifier is
expected to produce. Run with those, the journey measures the conversation machinery on its own,
deterministically, with no API key — which is what CI can run. Run with recorded classifier
outputs (``--fixtures``), it measures the model and the machinery together, and a wrong label
shows up as the turn it broke. Both go through the same runner.

*What the diary is.* A fixture, not a database. The tools read a ``ClinicDirectory`` and this
provides one in memory, so a journey costs a millisecond and the diary can be made to move
underneath a conversation — a slot taken between the offer and the yes is a scripted event here,
not a race nobody can reproduce.

*What is asserted.* Structural facts, never wording: the time offered, the day read back, the
reference quoted, whether an appointment now exists, and — for the safety journeys — what must
**not** appear. The tenant's Arabic templates are configuration and change without changing whether
the journey worked.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from apps.api.conversations.receptionist import handle
from apps.api.conversations.task import Task, TaskStatus
from apps.api.conversations.tools import (
    REGISTRY,
    CheckAvailability,
    CloseConversation,
    ConfirmBooking,
    ConversationCopy,
    Greet,
    HoldSlot,
    QuotePrice,
    Tool,
    configure_conversation_copy,
    current_copy,
)
from apps.api.core.clinic import (
    AvailabilitySlot,
    Booking,
    BookingOutcome,
    Branch,
    Service,
    booking_idempotency_key,
)
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.envelope import InboundTurn, OutboundAction

from packages.intents.schema import Vocabulary, vocabulary_for

# ── The case files ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TurnLabel:
    """What the classifier is expected to make of one message."""

    intent: str
    confidence: float = 0.95
    slots: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_result(cls, result: ClassificationResult) -> TurnLabel:
        return cls(
            intent=str(result.intent),
            confidence=result.confidence_intent,
            slots=dict(result.extracted_slots),
        )


@dataclass(frozen=True, slots=True)
class TurnExpectation:
    """What the patient must and must not receive on one turn.

    ``excludes`` is not the mirror image of ``includes``. It is the safety half: a screening block
    that names the disclosure back at the patient, or a booking reference in a reply that booked
    nothing, is a specific failure with a specific wrong string in it.
    """

    kind: str | None = None
    """``ask`` / ``confirm`` / ``say`` / ``handoff`` — ``OutboundAction.kind``."""

    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    bookings: int | None = None
    """How many appointments exist in the diary after this turn."""

    task_status: str | None = None


@dataclass(frozen=True, slots=True)
class JourneyTurn:
    """One inbound message, its expected label, and what must come back."""

    message: str
    label: TurnLabel
    expect: TurnExpectation
    take_slot: str | None = None
    """A slot somebody else books *before* this message arrives. The race, made reproducible."""


@dataclass(frozen=True, slots=True)
class JourneyCase:
    """One conversation, and why it is in the set."""

    id: str
    title: str
    turns: tuple[JourneyTurn, ...]
    tags: tuple[str, ...] = ()
    known_gap: str | None = None
    """Why this journey is expected to fail today.

    A journey written against behaviour that is *not built* is worth keeping and worth running:
    it is the difference between a gap somebody wrote down and a gap nobody has noticed. It is
    reported separately and does not count against the gate, so the set stays honest without
    holding a release hostage to work that has been consciously deferred.
    """


def _turn_from(record: dict[str, Any]) -> JourneyTurn:
    label = record["label"]
    expect = record.get("expect", {})
    return JourneyTurn(
        message=record["message"],
        label=TurnLabel(
            intent=label["intent"],
            confidence=float(label.get("confidence", 0.95)),
            slots=dict(label.get("slots", {})),
        ),
        expect=TurnExpectation(
            kind=expect.get("kind"),
            includes=tuple(expect.get("includes", ())),
            excludes=tuple(expect.get("excludes", ())),
            bookings=expect.get("bookings"),
            task_status=expect.get("task_status"),
        ),
        take_slot=record.get("take_slot"),
    )


def load_journeys(path: Path) -> list[JourneyCase]:
    """Parse the journey JSONL. One conversation per line."""
    cases: list[JourneyCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        cases.append(
            JourneyCase(
                id=record["id"],
                title=record["title"],
                turns=tuple(_turn_from(t) for t in record["turns"]),
                tags=tuple(record.get("tags", ())),
                known_gap=record.get("known_gap"),
            )
        )
    if not cases:
        raise ValueError(f"no journeys in {path}")
    return cases


# ── The diary ──────────────────────────────────────────────────────────────────────────────


class FixtureDiary:
    """The clinic's catalogue and one day of its diary, in memory (``ClinicDirectory``).

    The three things that make it worth having rather than mocking: a hold is visible to the
    conversation that placed it and opaque to every other one, a confirmation is idempotent on the
    same (conversation, slot), and a slot can be taken behind the receptionist's back between two
    turns. Those are the behaviours the journeys are about, and a stub that always says yes would
    pass every one of them.
    """

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.timezone: str = document["timezone"]
        self.now: datetime = datetime.fromisoformat(document["now"])
        self.reference_prefix: str = document.get("reference_prefix", "WB")
        self._next_serial: int = int(document.get("next_serial", 1))
        self.branches = tuple(
            Branch(
                external_id=b["external_id"],
                name=b["name"],
                area=b.get("area"),
                aliases=tuple(b.get("aliases", ())),
                placeholder=bool(b.get("placeholder", False)),
            )
            for b in document["branches"]
        )
        self.services = tuple(
            Service(
                code=s["code"],
                name=s["name"],
                category=s.get("category"),
                price_minor=int(s["price_minor"]),
                currency=s.get("currency", "EGP"),
                duration_minutes=int(s["duration_minutes"]),
                session_count=int(s.get("session_count", 1)),
                aliases=tuple(s.get("aliases", ())),
            )
            for s in document["services"]
        )
        self.slots = [
            AvailabilitySlot(
                external_id=s["external_id"],
                branch_external_id=s["branch_external_id"],
                service_code=s["service_code"],
                starts_at=datetime.fromisoformat(s["starts_at"]),
                ends_at=datetime.fromisoformat(s["ends_at"]),
                status=s.get("status", "open"),
            )
            for s in document["slots"]
        ]
        self.bookings: list[Booking] = []
        self.holds: dict[str, tuple[str, datetime]] = {}

    @classmethod
    def from_path(cls, path: Path) -> FixtureDiary:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def fresh(self) -> FixtureDiary:
        """A clean copy. Every journey starts from the diary as the clinic exported it."""
        return FixtureDiary(self.document)

    # ClinicDirectory ------------------------------------------------------------------

    def list_branches(self, tenant_id: str, *, active_only: bool = True) -> list[Branch]:
        return list(self.branches)

    def list_services(self, tenant_id: str, *, active_only: bool = True) -> list[Service]:
        return list(self.services)

    def available_slots(
        self,
        tenant_id: str,
        *,
        service_code: str,
        branch_external_id: str,
        on_date: date,
        timezone: str,
        now: datetime | None = None,
        conversation_id: str | None = None,
    ) -> list[AvailabilitySlot]:
        at = now or self.now
        zone = _zone(timezone)
        found = [
            slot
            for slot in self.slots
            if slot.service_code == service_code
            and slot.branch_external_id == branch_external_id
            and slot.status != "booked"
            and slot.starts_at.astimezone(zone).date() == on_date
            and not self._held_by_somebody_else(slot.external_id, conversation_id, at)
        ]
        return sorted(found, key=lambda slot: slot.starts_at)

    def hold_slot(
        self,
        tenant_id: str,
        *,
        slot_external_id: str,
        conversation_id: str,
        until: datetime,
        now: datetime | None = None,
    ) -> bool:
        at = now or self.now
        if self._held_by_somebody_else(slot_external_id, conversation_id, at):
            return False
        self.holds[slot_external_id] = (conversation_id, until)
        return True

    def confirm_booking(
        self,
        tenant_id: str,
        *,
        slot_external_id: str,
        conversation_id: str,
        reference_prefix: str,
        patient_name: str | None = None,
        patient_phone: str | None = None,
        now: datetime | None = None,
        buffer_minutes: int = 15,
    ) -> BookingOutcome:
        key = booking_idempotency_key(tenant_id, conversation_id, slot_external_id)
        for booking in self.bookings:
            if booking.idempotency_key == key:
                return BookingOutcome("already_confirmed", booking)

        slot = next((s for s in self.slots if s.external_id == slot_external_id), None)
        if slot is None:
            return BookingOutcome("slot_unknown")
        if slot.status == "booked":
            return BookingOutcome("slot_taken")

        booking = Booking(
            reference=f"{reference_prefix}-{self._next_serial:04d}",
            slot_external_id=slot_external_id,
            source="bot",
            patient_name=patient_name,
            patient_phone=patient_phone,
            conversation_id=conversation_id,
            idempotency_key=key,
        )
        self._next_serial += 1
        self.bookings.append(booking)
        self.take(slot_external_id)
        return BookingOutcome("confirmed", booking)

    # Scripted events ------------------------------------------------------------------

    def take(self, slot_external_id: str) -> None:
        """Somebody else books the slot. The only way to make the race reproducible."""
        self.slots = [
            replace(slot, status="booked") if slot.external_id == slot_external_id else slot
            for slot in self.slots
        ]

    def _held_by_somebody_else(
        self, slot_external_id: str, conversation_id: str | None, at: datetime
    ) -> bool:
        holder, until = self.holds.get(slot_external_id, (None, at))
        return holder is not None and holder != conversation_id and until > at


def _zone(name: str) -> Any:
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


# ── Running one conversation ───────────────────────────────────────────────────────────────


@contextmanager
def _tools_for(diary: FixtureDiary, copy: ConversationCopy) -> Iterator[None]:
    """Install the real tools against this diary, and put the registry back afterwards.

    The registry is a process-global service locator, so a journey has to borrow it rather than
    own it. The tools themselves are the shipped ones — a journey that passed against a stand-in
    would prove nothing about the demo.
    """
    installed: list[Tool] = [
        Greet(copy),
        CloseConversation(copy),
        CheckAvailability(diary, timezone=diary.timezone, copy=copy, clock=lambda: diary.now),
        QuotePrice(diary, timezone=diary.timezone, copy=copy, clock=lambda: diary.now),
        HoldSlot(diary, timezone=diary.timezone, copy=copy, clock=lambda: diary.now),
        ConfirmBooking(
            diary,
            timezone=diary.timezone,
            reference_prefix=diary.reference_prefix,
            copy=copy,
            clock=lambda: diary.now,
        ),
    ]
    previous = {tool.name: REGISTRY.get(tool.name) for tool in installed}
    previous_copy = current_copy()
    REGISTRY.update({tool.name: tool for tool in installed})
    # The receptionist reads its own two sentences — the read-back and the confirmation — from the
    # process-global copy rather than from a tool, so a journey that only filled the registry
    # would score the tenant's wording everywhere except the two turns at the centre of it.
    configure_conversation_copy(copy)
    try:
        yield
    finally:
        configure_conversation_copy(previous_copy)
        for name, tool in previous.items():
            if tool is None:
                REGISTRY.pop(name, None)
            else:
                REGISTRY[name] = tool


class _Continuity:
    """The clarifying-turn budget, as the deployed conversation store keeps it.

    Mirrors ``db/orchestration_repo.py``'s ``SqlAlchemyConversationStore``: replies count against
    the job now in flight, a message about something else abandons the old job and starts a fresh
    budget, and a job that has finished leaves nothing to spend. It is a mirror rather than the
    real thing because the real thing needs a database, and a journey that needed one would not be
    run often enough to be worth having — but the rule it mirrors is the one that decides whether
    a patient is asked a second question or handed to a person, so it is kept deliberately close.
    """

    def __init__(self) -> None:
        self.task: Task | None = None
        self.intent: str | None = None
        self.replies_since_task: int = 0

    @property
    def replies_sent(self) -> int:
        # With a job in flight the budget is what has been spent on it; with none, nothing has
        # been spent, because the budget belongs to the task the next message opens. Counting the
        # whole conversation instead is what handed a patient to a person immediately after their
        # booking, which this file found — see `SqlAlchemyConversationStore.begin` and
        # `test_db_adapters.py`, which pins the rule on the real store.
        return self.replies_since_task if self.intent is not None else 0

    def record(self, task: Task, action: OutboundAction) -> None:
        if self.intent is not None and self.intent != task.intent:
            self.intent = None  # the old job was left; its turns do not follow the new one
            self.replies_since_task = 0
        if self.intent is None:
            self.intent = task.intent
            self.replies_since_task = 0
        self.replies_since_task += 1

        if task.status in (TaskStatus.COMPLETED, TaskStatus.HANDED_OFF, TaskStatus.ABANDONED):
            self.task = None
            self.intent = None
            self.replies_since_task = 0
        else:
            self.task = task


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """What one turn produced, and what was wrong with it."""

    index: int
    message: str
    kind: str
    text: str
    failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class JourneyOutcome:
    """One conversation's result. ``first_failure`` is the turn the journey broke on."""

    case: JourneyCase
    turns: tuple[TurnOutcome, ...]
    bookings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return all(turn.ok for turn in self.turns)

    @property
    def first_failure(self) -> TurnOutcome | None:
        return next((turn for turn in self.turns if not turn.ok), None)


def _inbound(tenant_id: uuid.UUID, text: str, at: datetime, index: int) -> InboundTurn:
    return InboundTurn(
        tenant_id=tenant_id,
        channel="whatsapp",
        channel_thread_id="journey-thread",
        channel_identity="+201000000000",
        modality="text",
        text=text,
        received_at=at + timedelta(seconds=index),
        idempotency_key=f"journey-{index}",
    )


def _check(
    expect: TurnExpectation, action: OutboundAction, task: Task, bookings: int
) -> tuple[str, ...]:
    text = action.text or ""
    failures: list[str] = []
    if expect.kind is not None and action.kind != expect.kind:
        failures.append(f"expected a {expect.kind}, got a {action.kind}")
    for wanted in expect.includes:
        if wanted not in text:
            failures.append(f"missing {wanted!r}")
    for unwanted in expect.excludes:
        if unwanted in text:
            failures.append(f"said {unwanted!r}, which it must never say here")
    if expect.bookings is not None and bookings != expect.bookings:
        failures.append(f"expected {expect.bookings} appointment(s) in the diary, found {bookings}")
    if expect.task_status is not None and task.status.value != expect.task_status:
        failures.append(f"expected task {expect.task_status}, got {task.status.value}")
    return tuple(failures)


def run_journey(
    case: JourneyCase,
    diary: FixtureDiary,
    *,
    vocabulary: Vocabulary,
    copy: ConversationCopy | None = None,
    labels: dict[str, TurnLabel] | None = None,
) -> JourneyOutcome:
    """Play one conversation through the real receptionist and score every turn.

    ``labels`` replaces the journey file's own expected labels with recorded classifier output,
    keyed by message. A message the recording does not cover is a hard failure rather than a
    silent fall back to the written label — the point of running that way is to find out what the
    model actually said.
    """
    tenant_id = uuid.uuid4()
    conversation_id = str(uuid.uuid4())
    continuity = _Continuity()
    outcomes: list[TurnOutcome] = []
    wording = copy or ConversationCopy(
        closing_booking_confirmed="Booked ✅ your reference is {booking_reference}."
    )

    with _tools_for(diary, wording):
        for index, turn in enumerate(case.turns):
            if turn.take_slot is not None:
                diary.take(turn.take_slot)

            label = turn.label
            if labels is not None:
                if turn.message not in labels:
                    outcomes.append(
                        TurnOutcome(
                            index=index,
                            message=turn.message,
                            kind="-",
                            text="",
                            failures=("no recorded classification for this message",),
                        )
                    )
                    break
                label = labels[turn.message]

            action, task = asyncio.run(
                handle(
                    _inbound(tenant_id, turn.message, diary.now, index),
                    label.intent,
                    label.confidence,
                    dict(label.slots),
                    continuity.task,
                    vocabulary=vocabulary,
                    conversation_id=conversation_id,
                    turns_taken=continuity.replies_sent,
                )
            )
            continuity.record(task, action)
            outcomes.append(
                TurnOutcome(
                    index=index,
                    message=turn.message,
                    kind=action.kind,
                    text=action.text or "",
                    failures=_check(turn.expect, action, task, len(diary.bookings)),
                )
            )

    return JourneyOutcome(
        case=case,
        turns=tuple(outcomes),
        bookings=tuple(booking.reference for booking in diary.bookings),
    )


# ── The report ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JourneyReport:
    """What the whole set did. ``turn_accuracy`` is the gate-able number."""

    outcomes: tuple[JourneyOutcome, ...]
    label_source: str
    diary: str

    @property
    def gated(self) -> tuple[JourneyOutcome, ...]:
        """The journeys whose result is a verdict on the build, not on a known gap."""
        return tuple(o for o in self.outcomes if o.case.known_gap is None)

    @property
    def known_gaps(self) -> tuple[JourneyOutcome, ...]:
        return tuple(o for o in self.outcomes if o.case.known_gap is not None)

    @property
    def closed_gaps(self) -> tuple[JourneyOutcome, ...]:
        """A known gap that now passes. Delete the flag — the behaviour arrived."""
        return tuple(o for o in self.known_gaps if o.ok)

    @property
    def total(self) -> int:
        return len(self.gated)

    @property
    def passed(self) -> int:
        return sum(1 for outcome in self.gated if outcome.ok)

    @property
    def journey_accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def turns(self) -> int:
        return sum(len(outcome.turns) for outcome in self.gated)

    @property
    def turns_passed(self) -> int:
        return sum(1 for outcome in self.gated for turn in outcome.turns if turn.ok)

    @property
    def turn_accuracy(self) -> float:
        return self.turns_passed / self.turns if self.turns else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label_source": self.label_source,
            "diary": self.diary,
            "journeys": self.total,
            "known_gaps": [o.case.id for o in self.known_gaps],
            "journeys_passed": self.passed,
            "journey_accuracy": round(self.journey_accuracy, 4),
            "turns": self.turns,
            "turns_passed": self.turns_passed,
            "turn_accuracy": round(self.turn_accuracy, 4),
            "results": [
                {
                    "id": outcome.case.id,
                    "title": outcome.case.title,
                    "tags": list(outcome.case.tags),
                    "known_gap": outcome.case.known_gap,
                    "ok": outcome.ok,
                    "bookings": list(outcome.bookings),
                    "turns": [
                        {
                            "index": turn.index,
                            "message": turn.message,
                            "kind": turn.kind,
                            "text": turn.text,
                            "failures": list(turn.failures),
                        }
                        for turn in outcome.turns
                    ],
                }
                for outcome in self.outcomes
            ],
        }


def run_journeys(
    cases: Sequence[JourneyCase],
    diary: FixtureDiary,
    *,
    vocabulary: Vocabulary | None = None,
    copy: ConversationCopy | None = None,
    labels: dict[str, TurnLabel] | None = None,
    diary_name: str = "fixture",
) -> JourneyReport:
    """Every journey, each against its own clean copy of the diary."""
    vocab = vocabulary or vocabulary_for("clinics")
    outcomes = tuple(
        run_journey(case, diary.fresh(), vocabulary=vocab, copy=copy, labels=labels)
        for case in cases
    )
    return JourneyReport(
        outcomes=outcomes,
        label_source="recorded classifications" if labels else "the journey file's own labels",
        diary=diary_name,
    )
