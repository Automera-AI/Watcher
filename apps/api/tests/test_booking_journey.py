"""The booking journey, turn by turn (demo step 6).

This is the demo's transactional core and the thing that did not exist: greet, close, answer from
the knowledge base and hand off all worked, and *nothing* could check availability, quote a price,
hold a slot or write an appointment. The tests here are written as conversations rather than as
unit calls because every failure this step is about happens between turns — a detail agreed on one
message and forgotten by the next, a slot offered and then given away, a "تمام" that ends the
conversation instead of finishing it.

The directory is a fake with a diary in memory. What is under test is the receptionist's decisions;
what the *database* does under two conversations racing for one slot is ``test_clinic_repo.py``'s.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from packages.intents.schema import vocabulary_for

from apps.api.conversations import tools
from apps.api.conversations.receptionist import handle
from apps.api.conversations.task import NON_PROGRESS_TURNS_SLOT, Task, TaskStatus
from apps.api.conversations.tools import (
    REGISTRY,
    CheckAvailability,
    CloseConversation,
    ConfirmBooking,
    ConversationCopy,
    Greet,
    HoldSlot,
    QuotePrice,
)
from apps.api.core.clinic import (
    AvailabilitySlot,
    Booking,
    BookingOutcome,
    Branch,
    Service,
    booking_idempotency_key,
)
from apps.api.schemas.envelope import InboundTurn, OutboundAction

CLINICS = vocabulary_for("clinics")
TENANT_ID = uuid.uuid4()
CONVERSATION = str(uuid.uuid4())
OTHER_CONVERSATION = str(uuid.uuid4())
CAIRO = "Africa/Cairo"

#: Wednesday 2 September 2026 — the demo's own day, the one the script is written around.
WEDNESDAY = date(2026, 9, 2)
#: Noon Cairo on the Tuesday before it, as the classifier would see the message arrive.
NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

BRANCHES = (
    Branch(external_id="DC01", name="Maadi", area="Maadi, Cairo", aliases=("المعادي",)),
    Branch(external_id="DC02", name="New Cairo", area="Fifth Settlement"),
)
SERVICES = (
    Service(
        code="DT001",
        name="Basic Facial",
        price_minor=75_000,
        duration_minutes=45,
        aliases=("فاشيال بيسك",),
    ),
    Service(code="DT002", name="Facial", price_minor=75_000, duration_minutes=45),
    # Two of the three 12-session laser packages that all cost 16,350 in the real catalogue.
    Service(
        code="DT020",
        name="Laser Full Body 12 Sessions",
        price_minor=1_635_000,
        duration_minutes=60,
        session_count=12,
        aliases=("الليزر 12 جلسة", "لليزر 12 جلسة", "ليزر 12 جلسة"),
    ),
    Service(
        code="DT021",
        name="Laser Legs 12 Sessions",
        price_minor=1_635_000,
        duration_minutes=60,
        session_count=12,
        aliases=("الليزر 12 جلسة", "لليزر 12 جلسة", "ليزر 12 جلسة"),
    ),
    Service(
        code="DT029",
        name="Primelase 6-Sessions",
        price_minor=1_500_000,
        duration_minutes=60,
        session_count=6,
        aliases=("برايم ليز 6 جلسات",),
    ),
)


def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def _cairo(hour: int, minute: int = 0) -> datetime:
    """A wall-clock time on the demo's Wednesday, as an absolute moment."""
    return datetime(2026, 9, 2, hour, minute, tzinfo=_zone(CAIRO))


class _FakeDirectory:
    """A clinic diary in memory, with the same three failure modes the real one has."""

    def __init__(self, slots: list[AvailabilitySlot] | None = None) -> None:
        self.slots = slots if slots is not None else _default_diary()
        self.bookings: list[Booking] = []
        self.holds: dict[str, tuple[str, datetime]] = {}
        self.availability_queries: list[tuple[str, str, date]] = []
        self.next_serial = 266  # the workbook's highest is DC-0265

    def list_branches(self, tenant_id: str, *, active_only: bool = True) -> list[Branch]:
        return list(BRANCHES)

    def list_services(self, tenant_id: str, *, active_only: bool = True) -> list[Service]:
        return list(SERVICES)

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
        at = now or NOW
        self.availability_queries.append((service_code, branch_external_id, on_date))
        found = []
        for slot in self.slots:
            if slot.service_code != service_code or slot.branch_external_id != branch_external_id:
                continue
            if slot.starts_at.astimezone(_zone(timezone)).date() != on_date:
                continue
            if slot.status == "booked":
                continue
            holder, until = self.holds.get(slot.external_id, (None, at))
            if holder is not None and holder != conversation_id and until > at:
                continue
            found.append(slot)
        return sorted(found, key=lambda s: s.starts_at)

    def hold_slot(
        self,
        tenant_id: str,
        *,
        slot_external_id: str,
        conversation_id: str,
        until: datetime,
        now: datetime | None = None,
    ) -> bool:
        at = now or NOW
        holder, held_until = self.holds.get(slot_external_id, (None, at))
        if holder is not None and holder != conversation_id and held_until > at:
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
            reference=f"{reference_prefix}-{self.next_serial:04d}",
            slot_external_id=slot_external_id,
            source="bot",
            patient_name=patient_name,
            patient_phone=patient_phone,
            conversation_id=conversation_id,
            idempotency_key=key,
        )
        self.next_serial += 1
        self.bookings.append(booking)
        self.take(slot_external_id)
        return BookingOutcome("confirmed", booking)

    def take(self, slot_external_id: str) -> None:
        """Mark a slot booked behind the receptionist's back — somebody else got there first."""
        self.slots = [
            AvailabilitySlot(
                external_id=s.external_id,
                branch_external_id=s.branch_external_id,
                service_code=s.service_code,
                starts_at=s.starts_at,
                ends_at=s.ends_at,
                status="booked" if s.external_id == slot_external_id else s.status,
            )
            for s in self.slots
        ]


def _default_diary() -> list[AvailabilitySlot]:
    return [
        AvailabilitySlot(
            external_id=f"S{index:05d}",
            branch_external_id="DC01",
            service_code="DT001",
            starts_at=start,
            ends_at=start + timedelta(minutes=45),
        )
        for index, start in enumerate((_cairo(11), _cairo(16), _cairo(18)), start=1)
    ]


@pytest.fixture
def directory(monkeypatch: pytest.MonkeyPatch) -> _FakeDirectory:
    """The four booking tools, wired to an in-memory diary for the duration of one test."""
    fake = _FakeDirectory()
    copy = ConversationCopy(closing_booking_confirmed="Booked ✅ ref {booking_reference}.")
    for tool in (
        CloseConversation(copy),
        CheckAvailability(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        QuotePrice(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        HoldSlot(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        ConfirmBooking(fake, timezone=CAIRO, reference_prefix="DC", copy=copy, clock=lambda: NOW),
    ):
        monkeypatch.setitem(REGISTRY, tool.name, tool)
    return fake


#: An injectable — a ``screened_categories`` entry in ``clinics.yaml``. Booking one is a
#: clinician's decision whatever the patient says, so an availability offer for it must hand off at
#: catalogue resolution rather than be continued as a pending booking. Given a real alias and a
#: free slot so the offer path reaches a concrete result carrying ``service_category``.
_FILLER = Service(
    code="DT050",
    name="Filler",
    price_minor=500_000,
    duration_minutes=30,
    category="Injectables",
    aliases=("فيلر",),
)


class _InjectableDirectory(_FakeDirectory):
    """A diary that offers the catalogue plus one free injectable slot."""

    def __init__(self) -> None:
        super().__init__(
            slots=[
                AvailabilitySlot(
                    external_id="F00001",
                    branch_external_id="DC01",
                    service_code="DT050",
                    starts_at=_cairo(15),
                    ends_at=_cairo(15, 30),
                )
            ]
        )

    def list_services(self, tenant_id: str, *, active_only: bool = True) -> list[Service]:
        return [*SERVICES, _FILLER]


@pytest.fixture
def injectable_directory(monkeypatch: pytest.MonkeyPatch) -> _InjectableDirectory:
    """The booking tools wired to a diary whose only free slot is a screened injectable."""
    fake = _InjectableDirectory()
    copy = ConversationCopy(closing_booking_confirmed="Booked ✅ ref {booking_reference}.")
    for tool in (
        CloseConversation(copy),
        CheckAvailability(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        QuotePrice(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        HoldSlot(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        ConfirmBooking(fake, timezone=CAIRO, reference_prefix="DC", copy=copy, clock=lambda: NOW),
    ):
        monkeypatch.setitem(REGISTRY, tool.name, tool)
    return fake


#: The single-session Primelase the demo script books ("برايم ليز جلسة واحدة"). Given the bare
#: "برايم ليز" alias so the classifier's split into a service and a session count resolves: the
#: catalogue also holds the six-session package, and the count is what tells them apart.
_PRIMELASE_SINGLE = Service(
    code="DT030",
    name="Primelase Single Session",
    price_minor=310_000,
    duration_minutes=60,
    session_count=1,
    aliases=("برايم ليز",),
)


class _PrimelaseDirectory(_FakeDirectory):
    """A diary with free single-session Primelase slots at Maadi on the demo's Wednesday."""

    def __init__(self) -> None:
        super().__init__(
            slots=[
                AvailabilitySlot(
                    external_id=f"P{index:05d}",
                    branch_external_id="DC01",
                    service_code="DT030",
                    starts_at=start,
                    ends_at=start + timedelta(minutes=60),
                )
                for index, start in enumerate((_cairo(17), _cairo(18)), start=1)
            ]
        )

    def list_services(self, tenant_id: str, *, active_only: bool = True) -> list[Service]:
        return [*SERVICES, _PRIMELASE_SINGLE]


@pytest.fixture
def primelase_directory(monkeypatch: pytest.MonkeyPatch) -> _PrimelaseDirectory:
    """The booking tools wired to a diary that can actually book the demo script's service."""
    fake = _PrimelaseDirectory()
    copy = ConversationCopy(closing_booking_confirmed="Booked ✅ ref {booking_reference}.")
    for tool in (
        CloseConversation(copy),
        CheckAvailability(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        QuotePrice(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        HoldSlot(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        ConfirmBooking(fake, timezone=CAIRO, reference_prefix="DC", copy=copy, clock=lambda: NOW),
    ):
        monkeypatch.setitem(REGISTRY, tool.name, tool)
    return fake


_FULL_BODY_3 = Service(
    code="DT040",
    name="Full Body laser hair removal - 3 sessions",
    price_minor=500_000,
    duration_minutes=60,
    session_count=3,
    category="Laser",
    aliases=("فل بودي", "ليزر"),
)
_FULL_BODY_6 = Service(
    code="DT041",
    name="Full Body laser hair removal - 6 sessions",
    price_minor=900_000,
    duration_minutes=60,
    session_count=6,
    category="Laser",
    aliases=("فل بودي", "ليزر"),
)
_FULL_BODY_12 = Service(
    code="DT042",
    name="Full Body laser hair removal - 12 sessions",
    price_minor=1_600_000,
    duration_minutes=60,
    session_count=12,
    category="Laser",
    aliases=("فل بودي", "ليزر"),
)
_BIKINI_UNDERARM_6 = Service(
    code="DT043",
    name="Bikini & Underarm Laser Hair Removal - 6 Sessions",
    price_minor=700_000,
    duration_minutes=45,
    session_count=6,
    category="Laser",
    aliases=("ليزر بيكيني واندر ارم", "ليزر"),
)


class _ProductionTraceDirectory(_FakeDirectory):
    """The service choices present in the live trace, without inventing diary availability."""

    def __init__(self) -> None:
        super().__init__(slots=[])

    def list_services(self, tenant_id: str, *, active_only: bool = True) -> list[Service]:
        return [_FULL_BODY_3, _FULL_BODY_6, _FULL_BODY_12, _BIKINI_UNDERARM_6]


class _SixOctoberDirectory(_ProductionTraceDirectory):
    """A location catalogue whose numeric prefix must not validate by itself."""

    def list_branches(self, tenant_id: str, *, active_only: bool = True) -> list[Branch]:
        return [
            Branch(
                external_id="DC06",
                name="6th of October",
                area="Giza",
                aliases=("6 أكتوبر", "أكتوبر"),
            )
        ]


@pytest.fixture
def production_trace_directory(monkeypatch: pytest.MonkeyPatch) -> _ProductionTraceDirectory:
    fake = _ProductionTraceDirectory()
    for tool in (
        CheckAvailability(fake, timezone=CAIRO, clock=lambda: NOW),
        QuotePrice(fake, timezone=CAIRO, clock=lambda: NOW),
        HoldSlot(fake, timezone=CAIRO, clock=lambda: NOW),
        ConfirmBooking(fake, timezone=CAIRO, reference_prefix="DC", clock=lambda: NOW),
    ):
        monkeypatch.setitem(REGISTRY, tool.name, tool)
    return fake


@pytest.fixture
def six_october_directory(monkeypatch: pytest.MonkeyPatch) -> _SixOctoberDirectory:
    fake = _SixOctoberDirectory()
    monkeypatch.setitem(
        REGISTRY,
        "check_availability",
        CheckAvailability(fake, timezone=CAIRO, clock=lambda: NOW),
    )
    return fake


def _turn(text: str) -> InboundTurn:
    return InboundTurn(
        tenant_id=TENANT_ID,
        channel="whatsapp",
        channel_thread_id="thread-1",
        channel_identity="+201000000000",
        modality="text",
        text=text,
        received_at=NOW,
        idempotency_key=f"key-{text}",
    )


def _say(
    text: str,
    intent: str,
    slots: dict[str, str] | None = None,
    task: Task | None = None,
    turns_taken: int = 0,
) -> tuple[OutboundAction, Task]:
    return asyncio.run(
        handle(
            _turn(text),
            intent,
            0.95,
            slots or {},
            task,
            vocabulary=CLINICS,
            conversation_id=CONVERSATION,
            turns_taken=turns_taken,
        )
    )


# ── Current-message provenance for service and branch (live trace) ────────────────────────────


def test_classifier_branch_is_rejected_when_current_message_does_not_name_it(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    _action, task = _say(
        "محتاجة احجز ليزر",
        "booking_enquiry",
        {"service": "laser", "branch": "المعادي"},
    )

    assert "branch" not in task.slots


def test_bare_number_cannot_validate_classifier_selected_numeric_branch(
    six_october_directory: _SixOctoberDirectory,
) -> None:
    _action, task = _say(
        "عايزة 6 جلسات",
        "booking_enquiry",
        {"branch": "6 أكتوبر"},
    )

    assert "branch" not in task.slots


@pytest.mark.parametrize("message", ["6 أكتوبر", "أكتوبر", "فرع 6 أكتوبر"])
def test_location_words_validate_sixth_of_october_branch(
    six_october_directory: _SixOctoberDirectory, message: str
) -> None:
    _action, task = _say(
        message,
        "booking_enquiry",
        {"branch": "6 أكتوبر"},
    )

    assert task.slots["branch"] == "6 أكتوبر"


def test_broad_message_cannot_license_classifier_selected_specific_service(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    active = Task(
        intent="booking_enquiry",
        slots={"service": "laser"},
        vocabulary=CLINICS,
    )

    _action, task = _say(
        "بكرة ف المعادي",
        "booking_enquiry",
        {
            "service": "Bikini & Underarm Laser Hair Removal - 6 Sessions",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
        },
        active,
    )

    assert task.slots["branch"] == "المعادي"
    assert task.slots["requested_date"] == "2026-09-02"
    assert task.slots["service"] == "laser"


def test_ambiguous_laser_message_cannot_license_one_catalogue_sku(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    active = Task(
        intent="booking_enquiry",
        slots={"service": "laser"},
        vocabulary=CLINICS,
    )

    _action, task = _say(
        "ليزر",
        "booking_enquiry",
        {"service": "Bikini & Underarm Laser Hair Removal - 6 Sessions"},
        active,
    )

    assert task.slots["service"] == "laser"


def test_session_count_without_service_words_cannot_license_one_catalogue_sku(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    active = Task(
        intent="booking_enquiry",
        slots={"service": "laser"},
        vocabulary=CLINICS,
    )

    _action, task = _say(
        "بكرة الساعة 3",
        "booking_enquiry",
        {"service": _FULL_BODY_3.name, "requested_date": "2026-09-02"},
        active,
    )

    assert task.slots["service"] == "laser"


def test_current_message_may_change_service_to_supported_full_body_family(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    active = Task(
        intent="booking_enquiry",
        slots={"service": "laser"},
        vocabulary=CLINICS,
    )

    _action, task = _say(
        "عايزة فل بودي",
        "booking_enquiry",
        {"service": "Full Body"},
        active,
    )

    assert task.slots["service"] == "Full Body"


def test_current_message_may_select_one_exact_catalogue_service(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    active = Task(
        intent="booking_enquiry",
        slots={"service": "laser"},
        vocabulary=CLINICS,
    )
    selected = "Full Body laser hair removal - 3 sessions"

    _action, task = _say(
        selected,
        "booking_enquiry",
        {"service": selected},
        active,
    )

    assert task.slots["service"] == selected


def test_compact_production_trace_never_checks_the_classifier_invented_package(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    """The live sequence retains broad facts until the patient selects an exact package."""
    action, task = _say(
        "محتاجة احجز ليزر",
        "booking_enquiry",
        {"service": "laser", "branch": "المعادي"},
    )
    assert action.kind == "ask"
    assert task.slots["service"] == "laser"
    assert "branch" not in task.slots

    action, task = _say(
        "بكرة ف المعادي",
        "booking_enquiry",
        {
            "service": _BIKINI_UNDERARM_6.name,
            "branch": "المعادي",
            "requested_date": "2026-09-02",
        },
        task,
    )
    assert action.kind == "ask"
    assert task.slots["service"] == "laser"
    assert task.slots["branch"] == "المعادي"
    assert task.slots["requested_date"] == "2026-09-02"
    assert production_trace_directory.availability_queries == []

    action, task = _say(
        "عايزة فل بودي",
        "booking_enquiry",
        {"service": "Full Body"},
        task,
    )
    assert action.kind == "ask"
    assert task.slots["service"] == "Full Body"
    assert production_trace_directory.availability_queries == []

    action, task = _say(
        "Full Body laser hair removal - 3 sessions",
        "booking_enquiry",
        {"service": _FULL_BODY_3.name},
        task,
    )

    assert action.kind == "ask"
    assert task.status is not TaskStatus.HANDED_OFF
    assert task.slots["service"] == _FULL_BODY_3.name
    assert [query[0] for query in production_trace_directory.availability_queries] == [
        _FULL_BODY_3.code
    ]


# ── Demo-safe clarification limit (pre-demo Step 2) ──────────────────────────────────────────


def test_normal_booking_progress_is_not_cut_off_at_the_date_step(
    primelase_directory: _PrimelaseDirectory,
) -> None:
    """The demo script, one slot per turn, must reach real availability without a hand-off.

    ``service → branch → date → availability`` spends a reply on each step, so the old budget of
    two clarifying turns handed off the moment the date was asked for — one turn before the diary
    was ever consulted. The turn counter is advanced exactly as the worker advances it (one reply
    per turn), so this is the real budget the live path applies.
    """
    ask_service, task = _say("عايزة احجز", "booking_enquiry", {}, None, turns_taken=0)
    assert ask_service.kind == "ask"

    ask_branch, task = _say(
        "برايم ليز جلسة واحدة",
        "booking_enquiry",
        {"service": "برايم ليز", "session_count": "1"},
        task,
        turns_taken=1,
    )
    assert ask_branch.kind == "ask"

    ask_date, task = _say("المعادي", "booking_enquiry", {"branch": "المعادي"}, task, turns_taken=2)
    # The turn the old limit cut off: a branch was just given, the date is still outstanding.
    assert ask_date.kind == "ask"
    assert task.status is not TaskStatus.HANDED_OFF

    offer, task = _say(
        "بكرة", "booking_enquiry", {"requested_date": "2026-09-02"}, task, turns_taken=3
    )
    # Real diary availability, offered rather than handed off.
    assert offer.kind == "ask"
    assert task.status is not TaskStatus.HANDED_OFF
    assert "17:00" in (offer.text or "") and "18:00" in (offer.text or "")


def test_more_than_five_useful_booking_turns_do_not_trigger_handoff(
    production_trace_directory: _ProductionTraceDirectory,
) -> None:
    """Useful service refinement remains progress even after the old total-turn ceiling."""
    action, task = _say("محتاجة احجز", "booking_enquiry", {}, None, turns_taken=0)
    assert action.kind == "ask"

    action, task = _say("ليزر", "booking_enquiry", {"service": "laser"}, task, turns_taken=1)
    assert action.kind == "ask"

    action, task = _say("المعادي", "booking_enquiry", {"branch": "Maadi"}, task, turns_taken=2)
    assert action.kind == "ask"

    action, task = _say(
        "بكرة",
        "booking_enquiry",
        {"requested_date": "2026-09-02"},
        task,
        turns_taken=3,
    )
    assert action.kind == "ask"

    action, task = _say(
        "عايزة فل بودي",
        "booking_enquiry",
        {"service": "Full Body"},
        task,
        turns_taken=4,
    )
    assert action.kind == "ask"

    action, task = _say(
        "Full Body laser hair removal - 3 sessions",
        "booking_enquiry",
        {"service": _FULL_BODY_3.name},
        task,
        turns_taken=5,
    )

    assert action.kind != "handoff"
    assert task.status is not TaskStatus.HANDED_OFF
    assert task.slots["service"] == _FULL_BODY_3.name


def _to_primelase_offer(turns_taken_start: int = 0) -> tuple[OutboundAction, Task]:
    """Play the demo booking to the point where 17:00 / 18:00 have just been offered."""
    _, task = _say("عايزة احجز", "booking_enquiry", {}, None, turns_taken=turns_taken_start)
    _, task = _say(
        "برايم ليز جلسة واحدة",
        "booking_enquiry",
        {"service": "برايم ليز", "session_count": "1"},
        task,
        turns_taken=turns_taken_start + 1,
    )
    _, task = _say(
        "المعادي", "booking_enquiry", {"branch": "المعادي"}, task, turns_taken=turns_taken_start + 2
    )
    offer, task = _say(
        "بكرة",
        "booking_enquiry",
        {"requested_date": "2026-09-02"},
        task,
        turns_taken=turns_taken_start + 3,
    )
    return offer, task


@pytest.mark.parametrize("message", ["جلسة رقم 6", "6 مناطق", "6 جلسات الساعة 8"])
def test_a_bare_number_that_is_not_a_time_never_books_in_an_active_booking(
    primelase_directory: _PrimelaseDirectory, message: str
) -> None:
    """Codex blocker: a bare "6" beside other words is what ``parse_time`` read as 18:00.

    "جلسة رقم 6" (a session ordinal), "6 مناطق" (the substring-marker case: "مناطق" only *starts*
    with a meem) and "6 جلسات الساعة 8" (a stated 08:00, but the greedy read grabs the leading count
    "6" → 18:00) all carry a number that does not state the offered 18:00. In an active booking with
    17:00 / 18:00 already offered, that fabricated time must not be selected, held, read back or
    booked. A bare fragment out of context is classified ``unclear``, so this is the
    ``_read_as_answer`` path — the provenance guard drops the value there, and the turn does not
    advance to a hold or a confirmation.
    """
    offer, task = _to_primelase_offer()
    assert "17:00" in (offer.text or "") and "18:00" in (offer.text or "")

    action, after = _say(message, "unclear", {}, task, turns_taken=4)

    # No time was accepted, so nothing was held, nothing was read back, nothing was booked.
    assert "requested_time" not in after.slots
    assert action.kind != "confirm"
    assert primelase_directory.holds == {}
    assert primelase_directory.bookings == []

    # And an explicit "أيوه" afterwards has no read-back to agree to — still no booking.
    _confirmed, final = _say("أيوه", "thanks_closing", {}, after, turns_taken=5)
    assert "booking_reference" not in final.slots
    assert primelase_directory.bookings == []


def test_an_explicit_time_after_an_offer_still_books(
    primelase_directory: _PrimelaseDirectory,
) -> None:
    """The positive side: an explicit "الساعة ٦" is still read, held, confirmed and booked.

    Proves the narrowed time provenance did not break the real selection path — the guard keeps a
    time the message actually states.
    """
    _offer, task = _to_primelase_offer()

    read_back, task = _say("الساعة ٦", "unclear", {}, task, turns_taken=4)
    assert read_back.kind == "confirm"
    assert "18:00" in (read_back.text or "")
    assert primelase_directory.holds  # the slot was held while the patient confirms

    booked, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=5)
    assert booked.kind == "say"
    assert task.slots.get("booking_reference")
    assert len(primelase_directory.bookings) == 1


def test_repeated_non_progress_still_reaches_the_handoff_boundary() -> None:
    """Five consecutive replies that add no task fact still fetch a person."""
    action, task = _say("محتاجة احجز", "booking_enquiry", {}, None, turns_taken=0)
    assert action.kind == "ask"

    for attempt in range(1, 6):
        action, task = _say("مش عارفة", "booking_enquiry", {}, task, turns_taken=0)
        assert task.slots[NON_PROGRESS_TURNS_SLOT] == str(attempt)
        if attempt < 5:
            assert action.kind == "ask"

    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF


# ── The journey ────────────────────────────────────────────────────────────────────────────


def test_a_booking_runs_from_first_message_to_a_durable_reference(
    directory: _FakeDirectory,
) -> None:
    """The whole point of step 6, in four turns.

    Nothing in this sequence was possible before it: the times offered come from the diary, the
    read-back is answered rather than repeated, and the last turn writes an appointment and says
    the reference it was given.
    """
    offer, task = _say(
        "عاوزة أحجز فاشيال بيسك في المعادي بكرة",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    assert offer.kind == "ask"
    assert "11:00" in (offer.text or "") and "18:00" in (offer.text or "")

    read_back, task = _say("الساعة ٦", "booking_enquiry", {"requested_time": "18:00"}, task)
    assert read_back.kind == "confirm"
    assert "Wednesday 02 September" in (read_back.text or "")
    assert "18:00" in (read_back.text or "")
    # Held while the patient answers, so it cannot be given away underneath them.
    assert directory.holds["S00003"][0] == CONVERSATION

    booked, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=1)
    assert booked.kind == "say"
    assert "DC-0266" in (booked.text or "")
    assert task.status is TaskStatus.COMPLETED
    assert task.slots["booking_reference"] == "DC-0266"

    assert [b.reference for b in directory.bookings] == ["DC-0266"]
    assert directory.bookings[0].slot_external_id == "S00003"


def test_the_confirmed_booking_closing_is_reachable_at_last(directory: _FakeDirectory) -> None:
    """``closing_booking_confirmed`` has never been renderable: nothing supplied a reference.

    It is the one piece of tenant copy that can lie — it states an appointment exists — so this
    is also the test that the precondition is real rather than decorative.
    """
    task = Task(intent="thanks_closing", slots={"booking_reference": "DC-0266"}, vocabulary=CLINICS)
    action, _task = _say("شكراً", "thanks_closing", {}, task)
    assert action.text == "Booked ✅ ref DC-0266."


def test_tamam_finishes_the_booking_instead_of_ending_the_conversation(
    directory: _FakeDirectory,
) -> None:
    """The failure the handoff calls the most likely one on demo day.

    "تمام" classifies as ``thanks_closing`` — correctly, most of the time. Mid-read-back it means
    *yes, go ahead*, and before the dialogue-state rule the receptionist abandoned the booking and
    said goodbye to somebody one word from an appointment.
    """
    task = _complete_task()
    action, task = _say("تمام", "thanks_closing", {}, task, turns_taken=1)

    assert task.intent == "booking_enquiry"
    assert action.kind == "say"
    assert "DC-0266" in (action.text or "")


def test_a_thank_you_after_the_booking_still_closes_the_conversation(
    directory: _FakeDirectory,
) -> None:
    """The rule is narrow on purpose: away from a pending read-back, "تمام" still says goodbye."""
    done = Task(
        intent="booking_enquiry",
        slots=_booked_slots(),
        confirmed=set(_booked_slots()),
        vocabulary=CLINICS,
    )
    action, task = _say("تمام شكراً", "thanks_closing", {}, done)
    assert task.intent == "thanks_closing"
    assert action.kind == "say"


def test_saying_no_to_the_read_back_changes_nothing_and_asks(directory: _FakeDirectory) -> None:
    """A refusal agrees to nothing and guesses nothing about which detail was wrong."""
    task = _complete_task()
    action, task = _say("لا", "thanks_closing", {}, task, turns_taken=1)

    assert action.kind == "ask"
    assert task.confirmed == set()
    assert directory.bookings == []


def test_changing_a_detail_withdraws_the_agreement_to_it(directory: _FakeDirectory) -> None:
    """``absorb`` already dropped a confirmation when a value changed; now there is one to drop."""
    task = _complete_task()
    task.agree()
    assert task.unconfirmed == []

    task.absorb({"requested_time": "16:00"})
    assert "requested_time" in task.unconfirmed


def test_a_slot_taken_between_the_read_back_and_the_yes_is_not_booked_anyway(
    directory: _FakeDirectory,
) -> None:
    """The race the whole hold-and-confirm sequence exists for.

    The patient agreed to 18:00 and 18:00 has gone. What must not happen is an appointment at
    some other time, confirmed as though it were the one they chose.
    """
    task = _complete_task()
    directory.take("S00003")

    action, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=1)

    assert directory.bookings == []
    assert action.kind == "ask"
    assert "11:00" in (action.text or "")
    assert "requested_time" not in task.slots


def test_a_day_with_nothing_free_is_answered_rather_than_handed_off(
    directory: _FakeDirectory,
) -> None:
    """ "There is nothing on Thursday" is a real answer to a real question."""
    action, _task = _say(
        "احجزيلي فاشيال بيسك في المعادي الخميس",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-03"},
    )
    assert action.kind == "ask"
    assert "مفيش مواعيد فاضية" in (action.text or "")


def test_none_available_response_uses_failed_date_then_clears_only_date_bound_state(
    directory: _FakeDirectory,
) -> None:
    action, task = _say(
        "احجزيلي فاشيال بيسك في المعادي الخميس",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-03"},
    )

    assert action.kind == "ask"
    assert "Thursday 03 September" in (action.text or "")
    assert task.slots["service"] == "فاشيال بيسك"
    assert task.slots["branch"] == "المعادي"
    assert "requested_date" not in task.slots
    assert "requested_time" not in task.slots
    assert "requested_date" not in task.confirmed
    assert "requested_time" not in task.confirmed


@pytest.mark.parametrize("agreement", ["تمام", "ياريت"])
def test_affirmative_after_none_available_asks_for_new_date_without_rechecking_old_one(
    directory: _FakeDirectory, agreement: str
) -> None:
    _nothing_free, task = _say(
        "احجزيلي فاشيال بيسك في المعادي الخميس",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-03"},
    )
    calls_after_failure = list(directory.availability_queries)

    action, task = _say(agreement, "thanks_closing", {}, task)

    assert action.kind == "ask"
    assert action.text == "تمام، تحبي الحجز يكون يوم ايه؟"
    assert directory.availability_queries == calls_after_failure
    assert task.slots["service"] == "فاشيال بيسك"
    assert task.slots["branch"] == "المعادي"
    assert "requested_date" not in task.slots


def test_new_date_after_none_available_runs_a_fresh_availability_check(
    directory: _FakeDirectory,
) -> None:
    _nothing_free, task = _say(
        "احجزيلي فاشيال بيسك في المعادي الخميس",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-03"},
    )
    calls_after_failure = len(directory.availability_queries)

    action, task = _say(
        "الأربع",
        "booking_enquiry",
        {"requested_date": "2026-09-02"},
        task,
    )

    assert len(directory.availability_queries) == calls_after_failure + 1
    assert directory.availability_queries[-1][2] == WEDNESDAY
    assert action.kind == "ask"
    assert "11:00" in (action.text or "")
    assert task.slots["requested_date"] == "2026-09-02"


def test_repeating_same_unavailable_date_reaches_non_progress_handoff(
    directory: _FakeDirectory,
) -> None:
    _nothing_free, task = _say(
        "احجزيلي فاشيال بيسك في المعادي الخميس",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-03"},
    )
    assert task.slots[NON_PROGRESS_TURNS_SLOT] == "0"

    for attempt in range(1, 6):
        action, task = _say(
            "الخميس",
            "booking_enquiry",
            {"requested_date": "2026-09-03"},
            task,
        )
        assert task.slots[NON_PROGRESS_TURNS_SLOT] == str(attempt)
        assert "requested_date" not in task.slots
        if attempt < 5:
            assert action.kind == "ask"

    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF
    assert task.slots["service"] == "فاشيال بيسك"
    assert task.slots["branch"] == "المعادي"


def test_two_services_a_patient_cannot_tell_apart_are_asked_about_never_chosen(
    directory: _FakeDirectory,
) -> None:
    """ "Basic Facial" and "Facial" are both 750 for 45 minutes.

    Picking one quotes a real price for a treatment nobody asked about, and the matching price is
    what makes it invisible.
    """
    action, _task = _say("الليزر ١٢ جلسة بكام؟", "price_enquiry", {"service": "laser 12"})
    assert action.kind == "ask"
    assert "Laser Full Body 12 Sessions" in (action.text or "")
    assert "Laser Legs 12 Sessions" in (action.text or "")


def test_a_quote_states_the_currency_and_the_session_count(directory: _FakeDirectory) -> None:
    """``quoting.always_state``. One Primelase session is 3,100 and six are 15,000.

    Both are true prices for "Primelase", so a quote that omits which one it is is not imprecise —
    it is wrong by a factor of five, and the patient finds out at the counter.
    """
    action, _task = _say(
        "باكدج برايم ليز الست جلسات بكام؟",
        "price_enquiry",
        {"service": "برايم ليز 6 جلسات"},
    )
    assert action.kind == "say"
    assert "15,000 EGP" in (action.text or "")
    assert "6 sessions" in (action.text or "")


def test_availability_is_answered_with_times_the_diary_actually_holds(
    directory: _FakeDirectory,
) -> None:
    action, _task = _say(
        "في مواعيد فاضية للفاشيال بيسك في المعادي بكرة؟",
        "availability_check",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    assert action.kind == "say"
    for offered in ("11:00", "16:00", "18:00"):
        assert offered in (action.text or "")


# ── Task 3: a successful availability offer is the start of a booking ─────────────────────────


def test_a_successful_availability_offer_continues_as_a_pending_booking(
    directory: _FakeDirectory,
) -> None:
    """The context loss Task 3 closes, at the point it happened.

    An ``availability_check`` that reaches a concrete offer has answered the patient's question,
    but on the demo flow that question is the first half of a booking. Completing the task now
    would drop its service, branch and date from the active set — both ``get_active_task`` and the
    eval mirror key continuity off status — so the next turn ("الساعة ٧") would begin from nothing.
    The task is instead continued in place as a ``booking_enquiry`` holding exactly what the
    availability check collected, and is now only missing the time it just offered. The offer text
    the patient reads is unchanged.
    """
    offer, task = _say(
        "في ميعاد فاشيال بيسك في المعادي بكرة؟",
        "availability_check",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )

    assert offer.kind == "say"
    assert "11:00" in (offer.text or "") and "18:00" in (offer.text or "")
    assert task.intent == "booking_enquiry"
    assert task.status is TaskStatus.COLLECTING
    assert task.slots["service"] == "فاشيال بيسك"
    assert task.slots["branch"] == "المعادي"
    assert task.slots["requested_date"] == "2026-09-02"
    assert "requested_time" not in task.slots  # the time is what the next turn supplies


def test_the_chosen_time_after_an_offer_reaches_the_read_back_without_re_supplying_context(
    directory: _FakeDirectory,
) -> None:
    """The Task 3 flow end to end, with the second turn re-supplying nothing.

    Turn 2 is the bare offered time, labelled ``unclear`` with no slots — it carries no service,
    branch or date. It reaches the booking read-back only because the availability offer was kept
    alive as a pending booking that still held them.
    """
    _offer, task = _say(
        "في ميعاد فاشيال بيسك في المعادي بكرة؟",
        "availability_check",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    read_back, task = _say("الساعة ٦", "unclear", {}, task)

    assert read_back.kind == "confirm"
    assert "Wednesday 02 September" in (read_back.text or "")
    assert "18:00" in (read_back.text or "")
    assert task.intent == "booking_enquiry"
    assert task.slots["requested_time"] == "18:00"


def test_a_day_with_nothing_free_is_not_continued_as_a_pending_booking(
    directory: _FakeDirectory,
) -> None:
    """The guard is on a concrete offer, not on any availability_check that runs.

    "Nothing free on Thursday" is a real answer, not the start of a booking. The task must stay an
    ``availability_check`` — relabelling it ``booking_enquiry`` would read the next unrelated
    fragment into a booking the diary has no slot for.
    """
    action, task = _say(
        "في ميعاد فاشيال بيسك في المعادي الخميس؟",
        "availability_check",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-03"},
    )

    assert action.kind == "ask"
    assert "مفيش مواعيد فاضية" in (action.text or "")
    assert task.intent == "availability_check"


def test_an_ambiguous_service_on_availability_is_not_continued_as_a_pending_booking(
    directory: _FakeDirectory,
) -> None:
    """An ambiguous service is a "which did you mean?" question, and offers nothing.

    It reaches the availability result with ``ok`` False and no times, so it is not continued as a
    booking: the patient has not been shown a slot to book.
    """
    action, task = _say(
        "في مواعيد لليزر ١٢ جلسة في المعادي بكرة؟",
        "availability_check",
        {"service": "laser 12", "branch": "المعادي", "requested_date": "2026-09-02"},
    )

    assert action.kind == "ask"
    assert "Laser Full Body 12 Sessions" in (action.text or "")
    assert "Laser Legs 12 Sessions" in (action.text or "")
    assert task.intent == "availability_check"


def test_an_unrelated_intent_after_an_offer_starts_fresh_and_inherits_no_booking_slots(
    directory: _FakeDirectory,
) -> None:
    """The pending booking an offer creates is still dropped by a real change of subject.

    After the offer keeps an ``availability_check`` alive as a ``booking_enquiry``, a confidently
    classified unrelated intent (a price question) opens a fresh task that inherits none of the
    booking slots — the offer does not turn every following message into part of a booking.
    """
    _offer, task = _say(
        "في ميعاد فاشيال بيسك في المعادي بكرة؟",
        "availability_check",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    assert task.intent == "booking_enquiry"

    action, task = _say(
        "برايم ليز 6 جلسات بكام؟",
        "price_enquiry",
        {"service": "برايم ليز 6 جلسات"},
        task,
    )

    assert task.intent == "price_enquiry"
    assert "branch" not in task.slots
    assert "requested_date" not in task.slots
    assert action.kind == "say"
    assert "15,000" in (action.text or "")


# ── Task 3 safety: the clinical gate on the availability → pending-booking transition ─────────


def test_a_disclosure_on_a_successful_availability_offer_hands_off_and_never_books(
    directory: _FakeDirectory,
) -> None:
    """A pregnancy disclosure on the availability turn must stop the booking before it begins.

    "أنا حامل، في ميعاد فاشيال ...؟" is an ``availability_check`` — its terminal tool is
    ``check_availability``, not ``confirm_booking`` — so it is not ``_is_booking`` and the turn-text
    screen in ``handle`` never ran for it. The facial has free slots, so the offer is concrete and
    would otherwise be continued as a pending ``booking_enquiry``. The disclosure has to be caught
    at that transition: the conversation hands off, the task is never relabelled a booking, and
    nothing is written.
    """
    action, task = _say(
        "أنا حامل، في ميعاد فاشيال بيسك في المعادي بكرة؟",
        "availability_check",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )

    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF
    assert task.intent == "availability_check"  # never converted to a pending booking
    assert directory.holds == {}
    assert directory.bookings == []


def test_a_screened_category_availability_offer_hands_off_before_hold_or_read_back(
    injectable_directory: _InjectableDirectory,
) -> None:
    """A screened treatment (an injectable) with real availability must hand off at resolution.

    Injectables are ``screened_categories`` in ``clinics.yaml`` — a clinician's decision whatever
    the patient says. The diary holds a free filler slot, so the availability check returns a
    concrete offer carrying ``service_category`` "Injectables". That offer must not be continued as
    a pending booking: the category is screened the moment the catalogue names it, so the
    conversation hands off immediately, and hold and read-back are never reached.
    """
    action, task = _say(
        "في ميعاد فيلر في المعادي بكرة؟",
        "availability_check",
        {"service": "فيلر", "branch": "المعادي", "requested_date": "2026-09-02"},
    )

    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF
    assert task.intent == "availability_check"  # not converted to a pending booking
    assert injectable_directory.holds == {}  # never reached the hold placed at read-back
    assert injectable_directory.bookings == []


def test_confirming_twice_gives_the_same_appointment_back(directory: _FakeDirectory) -> None:
    """A duplicate delivery must not produce a second appointment or a second reference.

    Asserted on the tool rather than on a replayed conversation, because that is where the
    guarantee lives: the key is (tenant, conversation, slot), and the database holds it as a unique
    constraint (``test_clinic_repo.py``). The receptionist never gets that far on a replay — the
    slot is booked by then, and it re-offers what is left rather than confirming anything twice.
    """
    tool = REGISTRY["confirm_booking"]
    first = asyncio.run(
        tool.run(
            tenant_id=str(TENANT_ID),
            conversation_id=CONVERSATION,
            slot_external_id="S00003",
        )
    )
    second = asyncio.run(
        tool.run(
            tenant_id=str(TENANT_ID),
            conversation_id=CONVERSATION,
            slot_external_id="S00003",
        )
    )

    assert len(directory.bookings) == 1
    assert first.data is not None and second.data is not None
    assert second.data["booking_reference"] == first.data["booking_reference"] == "DC-0266"
    assert second.data["already_confirmed"] is True


def test_without_the_clinic_tools_a_booking_still_hands_off_rather_than_claiming_success() -> None:
    """A holiday-home deploy, or a worker process that never called ``configure_clinic``.

    The names are simply absent from the registry, and an unbuilt terminal tool is a hand-off —
    the step 0 behaviour, unchanged.
    """
    task = _complete_task()
    action, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=1)
    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF


def test_a_time_the_model_could_not_label_still_answers_the_question_it_answers(
    directory: _FakeDirectory,
) -> None:
    """ "الساعة ٦" after an offer is the appointment time, whatever the classifier made of it.

    Out of context it is two words naming an hour: no request, no treatment, no verb. Both model
    tiers label it ``unclear`` — the escalation model more firmly than the cheap one — and before
    this it switched tasks and fetched a person on the turn *after* the patient was offered a
    time. Running the journeys on real classifications is what surfaced it; with labels written by
    hand the turn always arrived as ``booking_enquiry``.

    Narrow on purpose: the message is offered to the one slot the task is waiting on, through the
    same normalisation the worker applies to the model's own output.
    """
    offer, task = _say(
        "عاوزة أحجز فاشيال بيسك في المعادي بكرة",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    assert offer.kind == "ask"

    read_back, task = _say("الساعة ٦", "unclear", {}, task)

    assert read_back.kind == "confirm"
    assert "18:00" in (read_back.text or "")
    assert task.intent == "booking_enquiry"  # the booking was not abandoned
    assert task.slots["requested_time"] == "18:00"


def test_a_message_that_answers_nothing_is_still_a_hand_off(directory: _FakeDirectory) -> None:
    """The rescue resolves a value or it does not happen. It is not a way to keep every task.

    An ``unclear`` message that does not read as the awaited slot is exactly what ``unclear``
    means, and the vocabulary's ceiling for it is a person.
    """
    _offer, task = _say(
        "عاوزة أحجز فاشيال بيسك في المعادي بكرة",
        "booking_enquiry",
        {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    action, task = _say("؟؟؟", "unclear", {}, task)

    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF


def test_the_confidence_in_a_label_the_model_did_not_choose_does_not_gate_the_task(
    directory: _FakeDirectory,
) -> None:
    """The read-back rule ended in the hand-off it exists to prevent, and this is why.

    "تمام" mid-booking is `unclear` at 0.3 once the escalation model has had it. The dialogue
    state correctly overrides the *intent* to the task's own — and then `decide_autonomy` gated a
    booking on how sure the model had been about a label it never applied, and fetched a person.
    The intent came from the conversation, so the model's confidence in something else has no
    standing over it.
    """
    task = _complete_task()
    booked, task = _say("تمام", "unclear", {}, task, turns_taken=1)

    assert booked.kind == "say"
    assert "DC-0266" in (booked.text or "")
    assert task.status is TaskStatus.COMPLETED


def test_the_session_count_tells_three_identical_package_names_apart(
    directory: _FakeDirectory,
) -> None:
    """ "برايم ليز 6 جلسات بكام؟" reaches the model as a service *and* a quantity.

    That split is correct — but it left the catalogue lookup holding "برايم ليز", which is three
    packages differing only by session count, and asking a clarifying question whose answer the
    patient had already given. The count narrows; it never picks, so a count matching two rows or
    none still asks.
    """
    quoted, _task = _say(
        "برايم ليز 6 جلسات بكام؟",
        "price_enquiry",
        {"service": "برايم ليز", "session_count": "6"},
    )

    assert quoted.kind == "say"
    assert "15,000" in (quoted.text or "")


def test_a_session_count_narrows_and_never_picks() -> None:
    """The half of the rule that keeps it safe, against the real shape of the catalogue.

    Three 12-session laser packages cost 16,350 apiece in the client's file, so a count that
    reaches more than one — or none, or is not a number — leaves the clarifying question exactly
    where it was. Only a count that identifies one package answers anything.
    """
    from apps.api.conversations.tools import _by_session_count

    six = Service(
        code="DT029", name="Primelase 6", price_minor=1, duration_minutes=1, session_count=6
    )
    twelve = Service(
        code="DT030", name="Primelase 12", price_minor=1, duration_minutes=1, session_count=12
    )
    other_twelve = Service(
        code="DT019", name="Full Body 12", price_minor=1, duration_minutes=1, session_count=12
    )

    found = _by_session_count((six, twelve), "6")
    assert found is not None and found.found is six

    assert _by_session_count((six, twelve), "3") is None  # matches nothing
    assert _by_session_count((twelve, other_twelve), "12") is None  # matches two
    assert _by_session_count((six, twelve), "ست") is None  # not a number
    assert _by_session_count((six, twelve), None) is None  # nothing was said


def test_the_read_back_and_the_confirmation_are_the_tenants_own_words(
    directory: _FakeDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two sentences the receptionist composes itself, in the tenant's language.

    Every other word a patient reads already came from configuration; these two were composed in
    English inside this module, which meant a clinic whose whole conversation is in Arabic got
    "Just to confirm:" and "That's booked." at the centre of its booking. Running the journey end
    to end is what surfaced it — see ``packages/eval/journeys.py``.

    ``{values}`` rather than ``{details}`` is the point of having both: the labels the default
    reads with are English words, and a read-back in Arabic wants the values on their own.
    """
    copy = ConversationCopy(
        confirm_read_back="تأكيد الحجز: {values} — صح كده؟",
        booking_confirmed="تم الحجز ✅ رقم الحجز: {booking_reference}",
    )
    monkeypatch.setattr(tools, "_COPY", copy)

    read_back, task = _say(
        "عاوزة أحجز فاشيال بيسك في المعادي بكرة الساعة ٦",
        "booking_enquiry",
        _booked_slots(),
    )
    assert read_back.kind == "confirm"
    assert (read_back.text or "").startswith("تأكيد الحجز:")
    assert "Just to confirm" not in (read_back.text or "")
    # The values, without the English slot labels the default reads with.
    assert "فاشيال بيسك" in (read_back.text or "") and "service" not in (read_back.text or "")

    booked, _task = _say("أيوه", "thanks_closing", {}, task, turns_taken=1)
    assert booked.text == "تم الحجز ✅ رقم الحجز: DC-0266"


def test_a_tenant_template_that_will_not_take_its_placeholder_degrades(
    directory: _FakeDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in a template must not lose the reply that says an appointment exists.

    The same degrade-don't-crash trade the rest of the copy makes: a ``KeyError`` here would drop
    the one message the patient came for, on a booking that has already been written.
    """
    monkeypatch.setattr(
        tools, "_COPY", ConversationCopy(booking_confirmed="تم الحجز {reference_number}")
    )
    task = _complete_task()
    booked, _task = _say("أيوه", "thanks_closing", {}, task, turns_taken=1)

    assert "DC-0266" in (booked.text or "")
    assert len(directory.bookings) == 1


def _booked_slots() -> dict[str, str]:
    return {
        "service": "فاشيال بيسك",
        "branch": "المعادي",
        "requested_date": "2026-09-02",
        "requested_time": "18:00",
    }


def _complete_task() -> Task:
    """A booking task with everything collected and the read-back outstanding."""
    return Task(intent="booking_enquiry", slots=_booked_slots(), vocabulary=CLINICS)


# ── Step 4: the deterministic Arabic fallback ────────────────────────────────────────────────
#
# The renderer (step 5) is only safe if what it falls back to is safe and presentation-ready, and
# the primary DermaClub booking path must not fall back to English. These tests run the scripted
# journey and its safety exits with a clinic that has configured its wording in Egyptian Arabic —
# the deterministic layer beneath any future generation — and assert no generic English *system*
# sentence survives. Catalogue names ("Primelase Single Session", "Maadi") and the English date
# formatter ("Wednesday 02 September") are acknowledged display-name gaps (deploy runbook §5.2),
# not system sentences, so the check is for the English *templates*, never for the absence of every
# Latin character.

#: A clinic whose whole conversation is configured in Egyptian Arabic — every seam a tenant can
#: set, including the four step-4 additions (branch/date/time asks are Arabic in code, so these
#: exercise the override; handoff/unbuilt/clarify_change/quick-replies are English in code, so these
#: are what makes the clinic path Arabic). No brand name is asserted; the point is the language.
_DERMACLUB_COPY = ConversationCopy(
    opening="أهلاً بيكي! تحبي أساعدك في ايه؟",
    opening_named="أهلاً {customer_name}! تحبي أساعدك في ايه؟",
    closing="شكراً ليكي! يومك سعيد.",
    closing_booking_confirmed="تم ✅ رقم الحجز {booking_reference}. مستنينك في الفرع.",
    availability_offer=(
        "متاح عندنا {times} لـ {service} في فرع {branch} يوم {date}. تحبي أحجزلك إمتى؟"
    ),
    availability_none=(
        "معلش، مفيش مواعيد فاضية لـ {service} في فرع {branch} يوم {date}. تحبي يوم تاني؟"
    ),
    choose_one="تقصدي أنهي واحدة؟ {options}",
    booking_taken="معلش، الميعاد ده اتحجز حالاً. تحبي أشوفلك المتاح؟",
    ask_service="تحبي تحجزي أنهي خدمة{branch}{date}؟",
    ask_branch="تحبي تحجزي في أنهي فرع؟",
    ask_date="الحجز يكون يوم ايه؟",
    ask_time="تحبي الميعاد الساعة كام؟",
    confirm_read_back="تأكيد الحجز: {values} — صح كده؟",
    booking_confirmed="تم الحجز ✅ رقم الحجز: {booking_reference}. مستنينك في الفرع.",
    handoff="هحوّلك لزميلي اللي هيقدر يساعدك حالاً.",
    unbuilt="خليني أراجع ده مع الفريق وأرجعلك حالاً.",
    clarify_change="تحبي أغيّر أنهي تفصيلة؟",
    confirm_yes="أيوه",
    confirm_no="لأ",
)

#: The English *system* sentences that must never appear on the configured Arabic path. Each is a
#: template this branch replaced or routed through the tenant seam — not a catalogue name or a date.
_ENGLISH_SYSTEM_MARKERS = (
    "Could you please provide",
    "connect you with someone",
    "check that with the team",
    "Just to confirm",
    "That's booked",
    "which detail should I change",
    "I can offer",
    "nothing free",
    "Which would you like",
    "How can I help",
    "You're very welcome",
    "Hello",
    "Thanks — I've noted",
)


def _assert_no_english_system_text(action: OutboundAction) -> None:
    text = action.text or ""
    for marker in _ENGLISH_SYSTEM_MARKERS:
        assert marker not in text, f"English system text {marker!r} leaked: {text!r}"
    assert action.quick_replies != ["Yes", "No"], "English quick replies leaked"


@pytest.fixture
def dermaclub(monkeypatch: pytest.MonkeyPatch) -> _PrimelaseDirectory:
    """The demo's own service and diary, wired with DermaClub's Arabic wording everywhere.

    The clinic tools carry their copy at construction (the offer, the no-availability answer); the
    receptionist reads ``current_copy()`` for the sentences it composes itself (the asks, the
    read-back, the confirmation, the hand-off), so both are set to the same copy — otherwise half
    the journey would be Arabic and half the neutral default.
    """
    fake = _PrimelaseDirectory()
    copy = _DERMACLUB_COPY
    for tool in (
        Greet(copy),
        CloseConversation(copy),
        CheckAvailability(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        QuotePrice(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        HoldSlot(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        ConfirmBooking(fake, timezone=CAIRO, reference_prefix="DC", copy=copy, clock=lambda: NOW),
    ):
        monkeypatch.setitem(REGISTRY, tool.name, tool)
    monkeypatch.setattr(tools, "_COPY", copy)
    return fake


def test_the_scripted_arabic_booking_journey_has_no_generic_english_system_text(
    dermaclub: _PrimelaseDirectory,
) -> None:
    """The release gate: the whole demo script, turn by turn, in Egyptian Arabic.

        صباح الخير / عايزة احجز / برايم ليز جلسة واحدة / المعادي / بكرة
        → real availability → one real time → read-back → explicit yes → booking reference

    Every system sentence is checked against ``_ENGLISH_SYSTEM_MARKERS``: the greeting, the three
    slot asks between the service and the diary (the two that used to be answered in English), the
    availability offer, the read-back and its quick-reply buttons, and the confirmation. Exactly one
    real booking is written and a durable reference comes back.
    """
    greet, _ = _say("صباح الخير", "greeting", {}, None)
    assert greet.kind == "say"
    _assert_no_english_system_text(greet)

    ask_service, task = _say("عايزة احجز", "booking_enquiry", {}, None, turns_taken=0)
    assert ask_service.kind == "ask"
    _assert_no_english_system_text(ask_service)

    ask_branch, task = _say(
        "برايم ليز جلسة واحدة",
        "booking_enquiry",
        {"service": "برايم ليز", "session_count": "1"},
        task,
        turns_taken=1,
    )
    assert ask_branch.kind == "ask"
    _assert_no_english_system_text(ask_branch)

    ask_date, task = _say("المعادي", "booking_enquiry", {"branch": "المعادي"}, task, turns_taken=2)
    assert ask_date.kind == "ask"
    _assert_no_english_system_text(ask_date)

    offer, task = _say(
        "بكرة", "booking_enquiry", {"requested_date": "2026-09-02"}, task, turns_taken=3
    )
    assert offer.kind == "ask"
    assert "17:00" in (offer.text or "") and "18:00" in (offer.text or "")
    _assert_no_english_system_text(offer)

    read_back, task = _say("الساعة ٥", "unclear", {}, task, turns_taken=4)
    assert read_back.kind == "confirm"
    assert "17:00" in (read_back.text or "")
    assert read_back.quick_replies == ["أيوه", "لأ"]
    _assert_no_english_system_text(read_back)

    booked, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=5)
    assert booked.kind == "say"
    assert "DC-0266" in (booked.text or "")
    _assert_no_english_system_text(booked)

    assert [b.reference for b in dermaclub.bookings] == ["DC-0266"]
    assert task.slots["booking_reference"] == "DC-0266"


def test_no_availability_answers_in_configured_arabic(dermaclub: _PrimelaseDirectory) -> None:
    """A day with nothing free is a real answer, and on the DermaClub path it is Arabic.

    The single-session Primelase diary holds only the demo's Wednesday, so the Thursday is empty —
    the no-availability answer, not a hand-off.
    """
    action, task = _say(
        "احجزيلي برايم ليز جلسة واحدة في المعادي الخميس",
        "booking_enquiry",
        {
            "service": "برايم ليز",
            "session_count": "1",
            "branch": "المعادي",
            "requested_date": "2026-09-03",
        },
    )
    assert action.kind == "ask"
    assert "مفيش مواعيد فاضية" in (action.text or "")
    _assert_no_english_system_text(action)
    assert task.status is not TaskStatus.HANDED_OFF
    assert dermaclub.bookings == []


def test_the_generic_handoff_is_arabic_when_configured(dermaclub: _PrimelaseDirectory) -> None:
    """The hand-off is a deterministic safety surface (never renderer-owned) and must be Arabic.

    Repeated non-progress on the same outstanding question reaches the configured hand-off boundary;
    the sentence it says is the tenant's Arabic ``handoff``, not the neutral English default.
    """
    action, task = _say("؟", "availability_check", {}, None, turns_taken=0)
    for _attempt in range(5):
        action, task = _say("؟", "availability_check", {}, task, turns_taken=0)
    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF
    assert action.text == _DERMACLUB_COPY.handoff
    _assert_no_english_system_text(action)


def test_the_unbuilt_fallback_is_arabic_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A built-flow intent whose tool is not registered takes the unbuilt path — in Arabic.

    This is the safety net under step 1: with the clinic tools registered a real availability
    request reaches the diary, but if ``check_availability`` is *not* wired (the bug step 1 fixed)
    an otherwise-actionable ``availability_check`` falls through to the unbuilt hand-off rather than
    claiming success. That fallback is a deterministic safety surface the renderer never owns, and
    on a configured clinic it must be Arabic. No diary fixture here on purpose: the tool is absent.
    """
    monkeypatch.setattr(tools, "_COPY", _DERMACLUB_COPY)
    assert "check_availability" not in REGISTRY  # the unbuilt precondition
    task = Task(
        intent="availability_check",
        slots={"service": "برايم ليز", "branch": "المعادي", "requested_date": "2026-09-02"},
        confirmed={"service", "branch", "requested_date"},
        vocabulary=CLINICS,
    )
    action, task = asyncio.run(
        handle(
            _turn("المواعيد المتاحة"),
            "availability_check",
            0.95,
            {},
            task,
            vocabulary=CLINICS,
            conversation_id=CONVERSATION,
        )
    )
    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF
    assert action.text == _DERMACLUB_COPY.unbuilt
    _assert_no_english_system_text(action)


def test_declining_the_read_back_asks_which_detail_in_arabic(
    dermaclub: _PrimelaseDirectory,
) -> None:
    """A "لأ" to the confirmation is on the Arabic booking path, so the follow-up is Arabic too."""
    _offer, task = _say("عايزة احجز", "booking_enquiry", {}, None, turns_taken=0)
    _, task = _say(
        "برايم ليز جلسة واحدة",
        "booking_enquiry",
        {"service": "برايم ليز", "session_count": "1"},
        task,
        turns_taken=1,
    )
    _, task = _say("المعادي", "booking_enquiry", {"branch": "المعادي"}, task, turns_taken=2)
    _, task = _say("بكرة", "booking_enquiry", {"requested_date": "2026-09-02"}, task, turns_taken=3)
    read_back, task = _say("الساعة ٥", "unclear", {}, task, turns_taken=4)
    assert read_back.kind == "confirm"

    declined, _ = _say("لأ", "unclear", {}, task, turns_taken=5)
    assert declined.kind == "ask"
    assert declined.text == _DERMACLUB_COPY.clarify_change
    _assert_no_english_system_text(declined)
