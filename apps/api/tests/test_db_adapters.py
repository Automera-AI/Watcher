"""Tests for the database implementations of the ingestion and orchestration ports (A4).

These are the objects that replace the in-memory doubles the pipeline has been tested against
since it was written. What is worth asserting is not that a row round-trips — it is the handful of
places where the row and the object it becomes are not the same shape: the history window and its
ordering (§7), a source kind that lives on another table, a nullable band written into a
non-nullable column, and a malformed rule that must not take a tenant's routing down with it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.audit.log import AuditEntry
from apps.api.conversations.task import Task, TaskStatus
from apps.api.db.engine import Database
from apps.api.db.models import (
    AuditLogRow,
    ChannelConfig,
    Classification,
    Conversation,
    CrmCacheRow,
    InboxItem,
    RuleRow,
    Source,
    TaskRow,
    Turn,
)
from apps.api.db.orchestration_repo import (
    SqlAlchemyAuditLog,
    SqlAlchemyClassificationWriter,
    SqlAlchemyConversationStore,
    SqlAlchemyCrmLookup,
    SqlAlchemyInboxWriter,
    SqlAlchemyMessageLoader,
    SqlAlchemyRulesProvider,
)
from apps.api.db.repository import SessionScopedMessageRepository
from apps.api.db.tenant_resolver import ChannelConfigTenantResolver, UnknownEndpoint
from apps.api.identity.resolver import IncomingContact
from apps.api.orchestration.ports import ClassificationDraft, InboxItemDraft
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import ConfidenceBand, InboxStatus, MessageType, SourceKind
from apps.api.schemas.envelope import InboundTurn, OutboundAction
from apps.api.schemas.message import MessageEnvelope

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _message(
    external_id: str,
    *,
    thread_id: str = "966500000000",
    text: str = "hello",
    at: datetime = NOW,
) -> MessageEnvelope:
    return MessageEnvelope(
        external_id=external_id,
        thread_id=thread_id,
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=MessageType.TEXT,
        body_text=text,
        received_at=at,
    )


# ── Ingestion repository, session per call ─────────────────────────────────────────────────


def test_session_scoped_repository_persists_across_sessions(database: Database) -> None:
    repository = SessionScopedMessageRepository(database.tenant_session)

    assert repository.exists(TENANT_A, "wamid.A") is False
    repository.save(TENANT_A, _message("wamid.A"))

    assert repository.exists(TENANT_A, "wamid.A") is True
    assert repository.exists(TENANT_B, "wamid.A") is False


# ── Message loader ─────────────────────────────────────────────────────────────────────────


def test_loader_returns_none_for_an_unknown_message(database: Database) -> None:
    """The consumer logs and moves on; it must not crash the worker on a missing row."""
    assert SqlAlchemyMessageLoader(database.tenant_session).load(TENANT_A, "wamid.missing") is None


def test_loader_round_trips_the_envelope(database: Database) -> None:
    SessionScopedMessageRepository(database.tenant_session).save(TENANT_A, _message("wamid.A"))

    loaded = SqlAlchemyMessageLoader(database.tenant_session).load(TENANT_A, "wamid.A")

    assert loaded is not None
    assert loaded.message.external_id == "wamid.A"
    assert loaded.message.body_text == "hello"
    assert loaded.message.sender_phone_e164 == "+966500000000"
    assert uuid.UUID(loaded.message_id)  # the persistent id, not the channel's
    assert loaded.history == []


def test_loader_reads_source_kind_from_the_sources_table(database: Database) -> None:
    """``messages`` has no ``source_kind``; whether a thread is a group is a fact about the thread.

    It matters because the classifier is told: in a group, the sender is not necessarily the
    person the message is about.
    """
    SessionScopedMessageRepository(database.tenant_session).save(TENANT_A, _message("wamid.A"))
    with database.session() as session:
        session.add(
            Source(tenant_id=uuid.UUID(TENANT_A), thread_id="966500000000", kind=SourceKind.GROUP)
        )

    loaded = SqlAlchemyMessageLoader(database.tenant_session).load(TENANT_A, "wamid.A")

    assert loaded is not None
    assert loaded.message.source_kind is SourceKind.GROUP


def test_loader_history_is_the_last_n_turns_oldest_first(database: Database) -> None:
    """§7: the last N in the same thread, by timestamp, oldest→newest.

    Both halves are load-bearing. Taking the *oldest* N hands the model the start of a long
    conversation instead of the part the message is answering, and ordering by insertion rather
    than by timestamp gets it wrong whenever a channel batches or retries a delivery.
    """
    repository = SessionScopedMessageRepository(database.tenant_session)
    # Saved newest-first, so insertion order and timestamp order disagree.
    for index in reversed(range(5)):
        repository.save(
            TENANT_A,
            _message(
                f"wamid.{index}", text=f"turn {index}", at=NOW - timedelta(minutes=10 - index)
            ),
        )
    repository.save(TENANT_A, _message("wamid.now", text="the message", at=NOW))

    loaded = SqlAlchemyMessageLoader(database.tenant_session, history_turns=3).load(
        TENANT_A, "wamid.now"
    )

    assert loaded is not None
    assert [turn.body_text for turn in loaded.history] == ["turn 2", "turn 3", "turn 4"]


def test_loader_history_does_not_cross_threads_or_tenants(database: Database) -> None:
    repository = SessionScopedMessageRepository(database.tenant_session)
    repository.save(TENANT_A, _message("wamid.other", thread_id="966599999999", at=NOW))
    repository.save(TENANT_B, _message("wamid.b", at=NOW))
    repository.save(TENANT_A, _message("wamid.now", at=NOW + timedelta(minutes=1)))

    loaded = SqlAlchemyMessageLoader(database.tenant_session).load(TENANT_A, "wamid.now")

    assert loaded is not None
    assert loaded.history == []


# ── Audit log and inbox ────────────────────────────────────────────────────────────────────


def test_audit_log_appends_the_decision_and_its_snapshot(database: Database) -> None:
    message_id = str(uuid.uuid4())
    destination_id = str(uuid.uuid4())

    SqlAlchemyAuditLog(database.tenant_session).write(
        AuditEntry(
            tenant_id=TENANT_A,
            message_id=message_id,
            action="auto_routed",
            actor="bot",
            classification_snapshot={"intent": "booking_enquiry"},
            destination_id=destination_id,
        )
    )

    with database.session() as session:
        row = session.query(AuditLogRow).one()
        assert row.action == "auto_routed"
        assert row.actor == "bot"
        assert row.classification_snapshot == {"intent": "booking_enquiry"}
        assert row.message_id == uuid.UUID(message_id)
        assert row.destination_id == uuid.UUID(destination_id)


def test_inbox_writer_points_at_the_classification(database: Database) -> None:
    """Where ``model_used`` went (A5).

    It used to be handed to this writer and dropped, because ``inbox_items`` has no such column.
    The classification row now exists, carries it, and the inbox item points at it.
    """
    classification_id = str(uuid.uuid4())

    SqlAlchemyInboxWriter(database.tenant_session).create(
        InboxItemDraft(
            tenant_id=TENANT_A,
            message_id=str(uuid.uuid4()),
            status=InboxStatus.AUTO_ROUTED,
            band=ConfidenceBand.HIGH,
            classification_id=classification_id,
        )
    )

    with database.session() as session:
        row = session.query(InboxItem).one()
        assert row.status == InboxStatus.AUTO_ROUTED.value
        assert row.band == ConfidenceBand.HIGH.value
        assert row.classification_id == uuid.UUID(classification_id)


def test_inbox_writer_files_a_bandless_draft_as_low(database: Database) -> None:
    """The unclassified path carries no band. A message the model could not read is not
    confident enough to act on, which is what ``low`` means — and the column is not nullable."""
    SqlAlchemyInboxWriter(database.tenant_session).create(
        InboxItemDraft(
            tenant_id=TENANT_A,
            message_id=str(uuid.uuid4()),
            status=InboxStatus.NEEDS_REVIEW,
            band=None,
        )
    )

    with database.session() as session:
        row = session.query(InboxItem).one()
        assert row.band == ConfidenceBand.LOW.value
        assert row.classification_id is None


# ── Rules and CRM cache ────────────────────────────────────────────────────────────────────


def _rule_row(tenant_id: str, name: str, *, priority: int = 0, enabled: bool = True) -> RuleRow:
    return RuleRow(
        tenant_id=uuid.UUID(tenant_id),
        name=name,
        conditions=[{"type": "sender_is_new"}],
        action={"destination_id": str(uuid.uuid4())},
        enabled=enabled,
        priority=priority,
    )


def test_rules_provider_returns_enabled_rules_in_priority_order(database: Database) -> None:
    with database.session() as session:
        session.add_all(
            [
                _rule_row(TENANT_A, "second", priority=10),
                _rule_row(TENANT_A, "first", priority=1),
                _rule_row(TENANT_A, "off", priority=0, enabled=False),
                _rule_row(TENANT_B, "other tenant", priority=0),
            ]
        )

    rules = SqlAlchemyRulesProvider(database.tenant_session)(TENANT_A)

    assert [rule.name for rule in rules] == ["first", "second"]


def test_rules_provider_skips_an_unparseable_rule(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """One bad jsonb row must not take the tenant's whole routing path down with it."""
    with database.session() as session:
        session.add(_rule_row(TENANT_A, "good", priority=1))
        broken = _rule_row(TENANT_A, "broken", priority=0)
        broken.conditions = [{"type": "no_such_condition"}]
        session.add(broken)

    with caplog.at_level(logging.WARNING):
        rules = SqlAlchemyRulesProvider(database.tenant_session)(TENANT_A)

    assert [rule.name for rule in rules] == ["good"]
    assert "skipping unparseable rule" in caplog.text


def test_crm_lookup_is_tenant_scoped(database: Database) -> None:
    with database.session() as session:
        session.add_all(
            [
                CrmCacheRow(
                    tenant_id=uuid.UUID(TENANT_A),
                    external_record_id="rec-1",
                    name="Sara",
                    company="Acme",
                    phones=["+966500000000"],
                ),
                CrmCacheRow(
                    tenant_id=uuid.UUID(TENANT_B),
                    external_record_id="rec-2",
                    name="Someone Else",
                    phones=["+966511111111"],
                ),
            ]
        )

    records = SqlAlchemyCrmLookup(database.tenant_session)(
        TENANT_A, IncomingContact(phone_e164="+966500000000")
    )

    assert [record.external_record_id for record in records] == ["rec-1"]
    assert records[0].phones == ["+966500000000"]


# ── Tenant resolution ──────────────────────────────────────────────────────────────────────


def _channel_config(tenant_id: str, external_id: str, *, enabled: bool = True) -> ChannelConfig:
    return ChannelConfig(
        tenant_id=uuid.UUID(tenant_id),
        kind="chat",
        external_id=external_id,
        config={},
        enabled=enabled,
    )


def test_tenant_resolver_maps_an_endpoint_to_its_tenant(database: Database) -> None:
    with database.session() as session:
        session.add(_channel_config(TENANT_A, "endpoint-1"))

    assert ChannelConfigTenantResolver(database.session)("endpoint-1") == TENANT_A


@pytest.mark.parametrize("endpoint", [None, "endpoint-unconfigured"])
def test_tenant_resolver_refuses_to_guess(database: Database, endpoint: str | None) -> None:
    """Guessing writes one customer's message into another's account. Raising loses nothing:
    the platform retries, and the retry succeeds once the configuration row exists."""
    with pytest.raises(UnknownEndpoint):
        ChannelConfigTenantResolver(database.session)(endpoint)


def test_tenant_resolver_ignores_a_disabled_endpoint(database: Database) -> None:
    with database.session() as session:
        session.add(_channel_config(TENANT_A, "endpoint-1", enabled=False))

    with pytest.raises(UnknownEndpoint):
        ChannelConfigTenantResolver(database.session)("endpoint-1")


# ── Continuity and classification telemetry (A5) ───────────────────────────────────────────


def _turn(
    external_id: str = "wamid.A",
    *,
    text: str = "Any rooms free in June?",
    thread_id: str = "966500000000",
) -> InboundTurn:
    return InboundTurn(
        tenant_id=uuid.UUID(TENANT_A),
        channel="whatsapp",
        channel_thread_id=thread_id,
        channel_identity="+966500000000",
        modality="text",
        text=text,
        received_at=NOW,
        idempotency_key=external_id,
    )


def _classification_result(intent: str = "booking_enquiry") -> ClassificationResult:
    return ClassificationResult.model_validate(
        {
            "intent": intent,
            "summary_one_line": "Guest asks about availability",
            "language": "en",
            "person_name": "Sara",
            "confidence_overall": 0.9,
            "confidence_intent": 0.9,
            "confidence_person": 0.9,
            "confidence_company": 0.9,
        }
    )


def test_classification_writer_records_the_result_and_its_telemetry(database: Database) -> None:
    """The table that had never been written to, and the two columns that kept it empty."""
    message_id = str(uuid.uuid4())

    row_id = SqlAlchemyClassificationWriter(database.tenant_session).record(
        ClassificationDraft(
            tenant_id=TENANT_A,
            message_id=message_id,
            result=_classification_result(),
            model_used="claude-haiku-4-5",
            prompt_version="v3",
            latency_ms=412,
        )
    )

    with database.session() as session:
        row = session.query(Classification).one()
        assert str(row.id) == row_id  # the id the inbox item points at
        assert row.message_id == uuid.UUID(message_id)
        assert (row.intent, row.language) == ("booking_enquiry", "en")
        assert row.person_name == "Sara"
        assert (row.model_used, row.prompt_version, row.latency_ms) == (
            "claude-haiku-4-5",
            "v3",
            412,
        )


def test_the_store_opens_a_conversation_and_records_what_was_said(database: Database) -> None:
    state = SqlAlchemyConversationStore(database.tenant_session).begin(_turn())

    assert state.task is None  # nothing in flight yet
    assert state.replies_sent == 0

    with database.session() as session:
        conversation = session.query(Conversation).one()
        assert str(conversation.id) == state.conversation_id
        assert conversation.channel_thread_id == "966500000000"
        # SQLite hands datetimes back naive; the value is what matters, not the tzinfo.
        assert conversation.last_turn_at is not None
        assert conversation.last_turn_at.replace(tzinfo=UTC) == NOW

        turn = session.query(Turn).one()
        assert (turn.direction, turn.body_text) == ("inbound", "Any rooms free in June?")


def test_the_second_message_finds_the_same_conversation_and_its_task(database: Database) -> None:
    """What continuity actually is: the job the previous turn opened comes back."""
    store = SqlAlchemyConversationStore(database.tenant_session)

    first = store.begin(_turn("wamid.1"))
    task = Task(intent="booking_enquiry", slots={"check_in": "4 June"})
    store.record_reply(first, _turn("wamid.1"), task, OutboundAction(kind="ask", text="How many?"))

    second = store.begin(_turn("wamid.2", text="two people"))

    assert second.conversation_id == first.conversation_id
    assert second.task is not None
    assert second.task.intent == "booking_enquiry"
    assert second.task.slots == {"check_in": "4 June"}  # survived the gap between messages
    assert second.replies_sent == 1  # and so did the clarifying-turn budget


def test_a_reply_is_recorded_once_even_if_the_message_is_processed_twice(
    database: Database,
) -> None:
    """A queue retry must not double the transcript, or double-count the turn budget."""
    store = SqlAlchemyConversationStore(database.tenant_session)
    action = OutboundAction(kind="ask", text="How many?")

    for _ in range(2):
        state = store.begin(_turn("wamid.1"))
        store.record_reply(state, _turn("wamid.1"), Task(intent="booking_enquiry"), action)

    with database.session() as session:
        assert session.query(Conversation).count() == 1
        assert session.query(Turn).count() == 2  # one inbound, one reply — not four
        assert session.query(TaskRow).count() == 1


def test_a_new_intent_abandons_the_old_task_rather_than_overwriting_it(
    database: Database,
) -> None:
    """A guest who changes the subject starts a new job, and the old one leaves the active set."""
    store = SqlAlchemyConversationStore(database.tenant_session)

    first = store.begin(_turn("wamid.1"))
    store.record_reply(
        first,
        _turn("wamid.1"),
        Task(intent="booking_enquiry"),
        OutboundAction(kind="ask", text="When?"),
    )

    second = store.begin(_turn("wamid.2", text="actually, where do I park?"))
    store.record_reply(
        second,
        _turn("wamid.2"),
        Task(intent="property_question"),
        OutboundAction(kind="say", text="In the basement."),
    )

    with database.session() as session:
        statuses = {row.intent: row.status for row in session.query(TaskRow).all()}
        assert statuses == {
            "booking_enquiry": TaskStatus.ABANDONED.value,
            "property_question": TaskStatus.COLLECTING.value,
        }

    # The new job starts with a clean budget: the turns spent on the abandoned one do not count.
    third = store.begin(_turn("wamid.3", text="which level?"))
    assert third.task is not None and third.task.intent == "property_question"
    assert third.replies_sent == 1
