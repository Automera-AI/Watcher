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
from apps.api.db.engine import Database
from apps.api.db.models import AuditLogRow, ChannelConfig, CrmCacheRow, InboxItem, RuleRow, Source
from apps.api.db.orchestration_repo import (
    SqlAlchemyAuditLog,
    SqlAlchemyCrmLookup,
    SqlAlchemyInboxWriter,
    SqlAlchemyMessageLoader,
    SqlAlchemyRulesProvider,
)
from apps.api.db.repository import SessionScopedMessageRepository
from apps.api.db.tenant_resolver import ChannelConfigTenantResolver, UnknownEndpoint
from apps.api.identity.resolver import IncomingContact
from apps.api.orchestration.ports import InboxItemDraft
from apps.api.schemas.enums import ConfidenceBand, InboxStatus, MessageType, SourceKind
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
    repository = SessionScopedMessageRepository(database.session)

    assert repository.exists(TENANT_A, "wamid.A") is False
    repository.save(TENANT_A, _message("wamid.A"))

    assert repository.exists(TENANT_A, "wamid.A") is True
    assert repository.exists(TENANT_B, "wamid.A") is False


# ── Message loader ─────────────────────────────────────────────────────────────────────────


def test_loader_returns_none_for_an_unknown_message(database: Database) -> None:
    """The consumer logs and moves on; it must not crash the worker on a missing row."""
    assert SqlAlchemyMessageLoader(database.session).load(TENANT_A, "wamid.missing") is None


def test_loader_round_trips_the_envelope(database: Database) -> None:
    SessionScopedMessageRepository(database.session).save(TENANT_A, _message("wamid.A"))

    loaded = SqlAlchemyMessageLoader(database.session).load(TENANT_A, "wamid.A")

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
    SessionScopedMessageRepository(database.session).save(TENANT_A, _message("wamid.A"))
    with database.session() as session:
        session.add(
            Source(tenant_id=uuid.UUID(TENANT_A), thread_id="966500000000", kind=SourceKind.GROUP)
        )

    loaded = SqlAlchemyMessageLoader(database.session).load(TENANT_A, "wamid.A")

    assert loaded is not None
    assert loaded.message.source_kind is SourceKind.GROUP


def test_loader_history_is_the_last_n_turns_oldest_first(database: Database) -> None:
    """§7: the last N in the same thread, by timestamp, oldest→newest.

    Both halves are load-bearing. Taking the *oldest* N hands the model the start of a long
    conversation instead of the part the message is answering, and ordering by insertion rather
    than by timestamp gets it wrong whenever a channel batches or retries a delivery.
    """
    repository = SessionScopedMessageRepository(database.session)
    # Saved newest-first, so insertion order and timestamp order disagree.
    for index in reversed(range(5)):
        repository.save(
            TENANT_A,
            _message(
                f"wamid.{index}", text=f"turn {index}", at=NOW - timedelta(minutes=10 - index)
            ),
        )
    repository.save(TENANT_A, _message("wamid.now", text="the message", at=NOW))

    loaded = SqlAlchemyMessageLoader(database.session, history_turns=3).load(TENANT_A, "wamid.now")

    assert loaded is not None
    assert [turn.body_text for turn in loaded.history] == ["turn 2", "turn 3", "turn 4"]


def test_loader_history_does_not_cross_threads_or_tenants(database: Database) -> None:
    repository = SessionScopedMessageRepository(database.session)
    repository.save(TENANT_A, _message("wamid.other", thread_id="966599999999", at=NOW))
    repository.save(TENANT_B, _message("wamid.b", at=NOW))
    repository.save(TENANT_A, _message("wamid.now", at=NOW + timedelta(minutes=1)))

    loaded = SqlAlchemyMessageLoader(database.session).load(TENANT_A, "wamid.now")

    assert loaded is not None
    assert loaded.history == []


# ── Audit log and inbox ────────────────────────────────────────────────────────────────────


def test_audit_log_appends_the_decision_and_its_snapshot(database: Database) -> None:
    message_id = str(uuid.uuid4())
    destination_id = str(uuid.uuid4())

    SqlAlchemyAuditLog(database.session).write(
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


def test_inbox_writer_records_the_assigned_destination(database: Database) -> None:
    destination_id = str(uuid.uuid4())

    SqlAlchemyInboxWriter(database.session).create(
        InboxItemDraft(
            tenant_id=TENANT_A,
            message_id=str(uuid.uuid4()),
            status=InboxStatus.AUTO_ROUTED,
            band=ConfidenceBand.HIGH,
            model_used="claude-haiku-4-5",
            assigned_destination_id=destination_id,
        )
    )

    with database.session() as session:
        row = session.query(InboxItem).one()
        assert row.status == InboxStatus.AUTO_ROUTED.value
        assert row.band == ConfidenceBand.HIGH.value
        assert row.assigned_action == {"destination_id": destination_id}


def test_inbox_writer_files_a_bandless_draft_as_low(database: Database) -> None:
    """The unclassified path carries no band. A message the model could not read is not
    confident enough to route, which is what ``low`` means — and the column is not nullable."""
    SqlAlchemyInboxWriter(database.session).create(
        InboxItemDraft(
            tenant_id=TENANT_A,
            message_id=str(uuid.uuid4()),
            status=InboxStatus.NEEDS_REVIEW,
            band=None,
            model_used=None,
            assigned_destination_id=None,
        )
    )

    with database.session() as session:
        row = session.query(InboxItem).one()
        assert row.band == ConfidenceBand.LOW.value
        assert row.assigned_action is None


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

    rules = SqlAlchemyRulesProvider(database.session)(TENANT_A)

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
        rules = SqlAlchemyRulesProvider(database.session)(TENANT_A)

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

    records = SqlAlchemyCrmLookup(database.session)(
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
