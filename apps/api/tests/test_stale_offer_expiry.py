"""Task 5: a stale availability offer must not be resumed into a booking — on the real store.

Task 3 keeps a *concrete* availability offer alive as a ``booking_enquiry`` left ``COLLECTING`` with
service, branch and date held and only ``requested_time`` still missing, so the patient's immediate
bare reply ("الساعة ٦") reaches the read-back without re-supplying context. That pending booking had
no freshness boundary, so a much later bare reply could inherit an old service/branch/date and reach
a hold or booking against a diary that has moved on.

These pin the fix through the **real persistence path** — the receptionist writes the offer proof
and the offer turn's own ``received_at`` onto the task, ``SqlAlchemyConversationStore.record_reply``
persists it, and the next turn's ``begin`` reads it back and decides freshness. There is no
manual timestamp editing: the offer instant is whatever ``received_at`` the offer turn carried, and
the age is measured against the next turn's ``received_at``, so the decision holds across a process
restart. The booking tools run against an in-memory diary (reused from ``test_booking_journey``);
the store runs on the conftest SQLite database, so it is the real adapter under test.

Three cases, exactly the ones the review asked to pin:

1. a recent concrete offer still resumes and still books (Task 3 flow intact);
2. a stale concrete offer leaves active continuity before the receptionist sees it, and nothing is
   held, read back, confirmed or booked from it;
3. a booking whose day had *no* concrete times is never marked, so it is not expired however long
   the patient takes — its service/branch survive while the failed date remains cleared.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from packages.intents.schema import vocabulary_for

from apps.api.conversations.receptionist import handle
from apps.api.conversations.task import AWAITING_ANOTHER_DATE_SLOT, Task, TaskStatus
from apps.api.conversations.tools import (
    REGISTRY,
    CheckAvailability,
    CloseConversation,
    ConfirmBooking,
    ConversationCopy,
    HoldSlot,
    QuotePrice,
)
from apps.api.db.engine import Database
from apps.api.db.models import TaskRow
from apps.api.db.orchestration_repo import SqlAlchemyConversationStore
from apps.api.schemas.envelope import InboundTurn, OutboundAction
from apps.api.tests.test_booking_journey import CAIRO, NOW, _FakeDirectory

CLINICS = vocabulary_for("clinics")
TENANT_ID = uuid.uuid4()
MAX_AGE = CLINICS.quoting.max_age_seconds  # the clinic's own freshness contract; no second TTL

#: The pending booking a concrete facial offer at Maadi on the demo Wednesday leaves behind.
_OFFER_SLOTS = {"service": "فاشيال بيسك", "branch": "المعادي", "requested_date": "2026-09-02"}


@pytest.fixture
def diary() -> _FakeDirectory:
    """The booking tools wired to an in-memory diary for one test (like ``test_booking_journey``).

    The tool clock is fixed at ``NOW`` so the diary's slots stay bookable regardless of a resume
    turn's timestamp — the point under test is the *offer's* age by event time, not a diary that
    changed underneath it.
    """
    fake = _FakeDirectory()
    copy = ConversationCopy(closing_booking_confirmed="Booked ✅ ref {booking_reference}.")
    for tool in (
        CloseConversation(copy),
        CheckAvailability(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        QuotePrice(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        HoldSlot(fake, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        ConfirmBooking(fake, timezone=CAIRO, reference_prefix="DC", copy=copy, clock=lambda: NOW),
    ):
        REGISTRY[tool.name] = tool  # conftest's autouse fixture restores REGISTRY afterwards
    return fake


def _turn(text: str, *, received_at: datetime, key: str) -> InboundTurn:
    return InboundTurn(
        tenant_id=TENANT_ID,
        channel="whatsapp",
        channel_thread_id="thread-1",
        channel_identity="+201000000000",
        modality="text",
        text=text,
        received_at=received_at,
        idempotency_key=key,
    )


def _step(
    store: SqlAlchemyConversationStore,
    *,
    text: str,
    intent: str,
    slots: dict[str, str],
    received_at: datetime,
    key: str,
) -> tuple[OutboundAction, Task | None]:
    """One whole worker step against the real store: begin → receptionist → record_reply."""
    turn = _turn(text, received_at=received_at, key=key)
    state = store.begin(turn)
    action, task = asyncio.run(
        handle(
            turn,
            intent,
            0.95,
            slots,
            state.task,
            vocabulary=CLINICS,
            conversation_id=state.conversation_id,
            turns_taken=state.replies_sent,
        )
    )
    store.record_reply(state, turn, task, action)
    return action, state.task


def _active_row(database: Database) -> TaskRow | None:
    with database.session() as session:
        return session.query(TaskRow).filter(TaskRow.status == "collecting").one_or_none()


def test_a_recent_concrete_offer_resumes_and_still_books(
    database: Database, diary: _FakeDirectory
) -> None:
    """Recent concrete offer: the Task 3 read-back → confirmation → booking flow is intact.

    The offer turn produces real times through the availability tool and is persisted normally; the
    bare time arrives within ``max_age_seconds`` (measured off the offer turn's own
    ``received_at``), so the pending booking resumes and books, reference and all.
    """
    store = SqlAlchemyConversationStore(database.tenant_session, vocabulary=CLINICS)

    offer, _ = _step(
        store,
        text="في ميعاد فاشيال بيسك في المعادي بكرة؟",
        intent="availability_check",
        slots=_OFFER_SLOTS,
        received_at=NOW,
        key="offer",
    )
    assert offer.kind == "say"
    assert "11:00" in (offer.text or "") and "18:00" in (offer.text or "")
    # The offer proof and its event timestamp were persisted on the task, not a wall-clock column.
    row = _active_row(database)
    assert row is not None and row.intent == "booking_enquiry"
    assert row.slots["availability_offered_at"] == NOW.isoformat()

    within = NOW + timedelta(seconds=MAX_AGE - 100)
    read_back, _ = _step(
        store, text="الساعة ٦", intent="unclear", slots={}, received_at=within, key="time"
    )
    assert read_back.kind == "confirm"
    assert "18:00" in (read_back.text or "")
    assert diary.holds  # the slot was held while the patient answers

    booked, _ = _step(
        store,
        text="أيوه",
        intent="thanks_closing",
        slots={},
        received_at=within + timedelta(seconds=5),
        key="yes",
    )
    assert booked.kind == "say"
    assert [b.reference for b in diary.bookings] == ["DC-0266"]


def test_a_stale_concrete_offer_is_dropped_before_the_receptionist_and_never_books(
    database: Database, diary: _FakeDirectory
) -> None:
    """Stale concrete offer: it leaves active continuity, and nothing is held, read back or booked.

    The identical offer is persisted normally. The next bare time arrives more than
    ``max_age_seconds`` after the offer turn's ``received_at``, so ``begin`` drops the pending
    booking (marks it abandoned) before the receptionist sees it. The fresh ``unclear`` turn then
    hands off: no old service/branch/date is inherited, no hold, no read-back, no booking, no
    reference.
    """
    store = SqlAlchemyConversationStore(database.tenant_session, vocabulary=CLINICS)

    _step(
        store,
        text="في ميعاد فاشيال بيسك في المعادي بكرة؟",
        intent="availability_check",
        slots=_OFFER_SLOTS,
        received_at=NOW,
        key="offer",
    )
    assert _active_row(database) is not None  # the pending booking exists before the gap

    stale = NOW + timedelta(seconds=MAX_AGE + 100)
    action, resumed_task = _step(
        store, text="الساعة ٦", intent="unclear", slots={}, received_at=stale, key="time"
    )

    assert resumed_task is None  # the store handed the receptionist nothing to resume
    assert action.kind == "handoff"
    assert diary.holds == {}  # nothing was held
    assert diary.bookings == []  # nothing was booked

    with database.session() as session:
        rows = {row.intent: row.status for row in session.query(TaskRow).all()}
    # The stale booking left the active set; the fresh unclear turn opened no pending booking.
    assert rows.get("booking_enquiry") == TaskStatus.ABANDONED.value
    assert all(
        status != TaskStatus.COLLECTING.value or intent != "booking_enquiry"
        for intent, status in rows.items()
    )


def test_a_no_availability_booking_is_never_expired_however_long_it_waits(
    database: Database, diary: _FakeDirectory
) -> None:
    """No concrete offer was ever made, so Task 5 must not expire it — the review's blocker 2.

    A booking for a day with nothing free stays ``booking_enquiry / COLLECTING`` waiting for
    ``requested_time`` — the same shape as a real pending offer — but it was offered no times, so it
    carries no offer proof. Even long past ``max_age_seconds`` it remains active with the
    patient's service and branch, while the failed date remains cleared for a fresh choice.
    """
    store = SqlAlchemyConversationStore(database.tenant_session, vocabulary=CLINICS)

    nothing_free = {**_OFFER_SLOTS, "requested_date": "2026-09-03"}  # a Thursday with no slots
    ask, _ = _step(
        store,
        text="احجزيلي فاشيال بيسك في المعادي الخميس",
        intent="booking_enquiry",
        slots=nothing_free,
        received_at=NOW,
        key="offer",
    )
    assert ask.kind == "ask"
    assert "مفيش مواعيد فاضية" in (ask.text or "")
    row = _active_row(database)
    assert row is not None
    assert "availability_offered_at" not in row.slots  # no proof, because nothing was offered

    long_after = NOW + timedelta(seconds=MAX_AGE * 10)
    resumed = store.begin(_turn("follow up", received_at=long_after, key="later"))

    assert resumed.task is not None
    assert resumed.task.intent == "booking_enquiry"
    assert resumed.task.status is TaskStatus.COLLECTING
    assert resumed.task.slots["service"] == "فاشيال بيسك"
    assert resumed.task.slots["branch"] == "المعادي"
    assert "requested_date" not in resumed.task.slots
    assert resumed.task.slots[AWAITING_ANOTHER_DATE_SLOT] == "1"
    assert _active_row(database) is not None  # still active, not abandoned


def test_a_new_day_with_no_availability_clears_the_earlier_offer_proof(
    database: Database, diary: _FakeDirectory
) -> None:
    """Blocker (round 3): an offer for A must not survive a re-evaluation for B that offers nothing.

    A concrete offer for the demo Wednesday marks the task. The patient then moves the booking to a
    Thursday with nothing free — a fresh availability evaluation that returns no concrete times. The
    earlier proof must be cleared then and there, not left orphaned on the task: otherwise a much
    later bare time would expire this booking (using A's stale timestamp) and discard the
    service/branch state retained after B failed.
    """
    store = SqlAlchemyConversationStore(database.tenant_session, vocabulary=CLINICS)

    offer, _ = _step(
        store,
        text="في ميعاد فاشيال بيسك في المعادي بكرة؟",
        intent="availability_check",
        slots=_OFFER_SLOTS,
        received_at=NOW,
        key="offer",
    )
    assert offer.kind == "say"
    marked = _active_row(database)
    assert marked is not None
    assert marked.slots["availability_offered_at"] == NOW.isoformat()

    # Move to a Thursday with nothing free: a real re-evaluation, no concrete times offered.
    reoffer, _ = _step(
        store,
        text="لأ خليها الخميس",
        intent="booking_enquiry",
        slots={"requested_date": "2026-09-03"},
        received_at=NOW + timedelta(seconds=60),
        key="change-day",
    )
    assert reoffer.kind == "ask"
    assert "مفيش مواعيد فاضية" in (reoffer.text or "")
    row = _active_row(database)
    assert row is not None
    assert "availability_offered_at" not in row.slots  # A's proof was cleared, not orphaned onto B
    assert "requested_date" not in row.slots
    assert row.slots[AWAITING_ANOTHER_DATE_SLOT] == "1"

    # A much later bare time (well past the window measured from A's offer) must NOT expire B.
    long_after = NOW + timedelta(seconds=MAX_AGE + 200)
    resumed = store.begin(_turn("الساعة ٦", received_at=long_after, key="late-time"))
    assert resumed.task is not None  # B was not wrongly abandoned
    assert "requested_date" not in resumed.task.slots
    assert resumed.task.slots["service"] == "فاشيال بيسك"
    assert resumed.task.slots["branch"] == "المعادي"
    assert _active_row(database) is not None


def _corrupt_offer_marker(database: Database, value: object) -> None:
    """Overwrite the persisted offer marker with a malformed value, as storage/JSON drift might."""
    with database.session() as session:
        row = session.query(TaskRow).filter(TaskRow.status == "collecting").one()
        slots: dict[str, object] = dict(row.slots)
        slots["availability_offered_at"] = value
        row.slots = slots  # type: ignore[assignment]  # deliberately malformed for the test


@pytest.mark.parametrize(
    "bad_marker", ["not-a-timestamp", 1725181200], ids=["unparseable", "wrong_type"]
)
def test_a_malformed_offer_marker_is_treated_as_stale_not_resumed_forever(
    database: Database, diary: _FakeDirectory, bad_marker: object
) -> None:
    """Blocker (round 3): a present-but-unusable marker must expire, never resume indefinitely.

    A real concrete offer is persisted, then its marker is corrupted to a value that cannot be read
    as a timestamp — an unparseable string, or a non-string a JSON round-trip could leave behind.
    On the next turn the task is still the post-offer waiting-for-time shape, so its offer age
    matters; because that age can no longer be trusted, the booking is expired (dropped from active
    continuity) rather than resumed forever or raising a ``TypeError``.
    """
    store = SqlAlchemyConversationStore(database.tenant_session, vocabulary=CLINICS)

    _step(
        store,
        text="في ميعاد فاشيال بيسك في المعادي بكرة؟",
        intent="availability_check",
        slots=_OFFER_SLOTS,
        received_at=NOW,
        key="offer",
    )
    _corrupt_offer_marker(database, bad_marker)

    # Even inside the freshness window, an unusable marker cannot prove freshness, so it is stale.
    within = NOW + timedelta(seconds=MAX_AGE - 100)
    resumed = store.begin(_turn("الساعة ٦", received_at=within, key="time"))

    assert resumed.task is None  # not resumed; no TypeError raised
    with database.session() as session:
        row = session.query(TaskRow).one()
        assert row.status == TaskStatus.ABANDONED.value
