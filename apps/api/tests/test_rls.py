"""Tests for tenant isolation in the database itself (roadmap B2).

Three layers, because RLS can fail in three unrelated ways and only one of them is visible without
a Postgres.

* **The stamp.** ``set_current_tenant`` has to issue the setting on Postgres and stay silent on
  SQLite, where the test suite lives and where the statement does not parse.
* **The wiring.** Every adapter has to open its session *as* the tenant it is acting for. This is
  the layer that would rot silently: a new adapter, or a new method on an old one, that opens an
  unstamped session still passes every other test in the tree — its queries filter by tenant in
  Python and the rows come back. Under RLS in production it would return nothing at all, and the
  first thing anyone would learn is that a guest's message went unanswered. So the assertion here
  is on the tenant the scope was *called with*, per adapter, per method.
* **The policies.** Whether Postgres actually refuses. That one needs a Postgres with migration
  ``004`` applied and a login-enabled ``watcher_app``, so it is opt-in via
  ``WATCHER_RLS_DATABASE_URL`` and skipped everywhere else — including CI, which ships no Postgres
  driver. It is the only test that can prove the claim, which is why it is written down rather than
  left as a thing someone once checked by hand.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from apps.api.audit.log import AuditEntry
from apps.api.clinic.importer import CataloguePlan
from apps.api.conversations.task import Task, TaskStatus
from apps.api.core.clinic import Branch, Service
from apps.api.db.clinic_repo import SqlAlchemyClinicRepository
from apps.api.db.engine import (
    TENANT_SETTING,
    Database,
    create_db_engine,
    set_current_tenant,
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
from apps.api.identity.resolver import IncomingContact
from apps.api.orchestration.ports import ClassificationDraft, InboxItemDraft
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import ConfidenceBand, InboxStatus, MessageType, SourceKind
from apps.api.schemas.envelope import InboundTurn, OutboundAction
from apps.api.schemas.message import MessageEnvelope

TENANT = str(uuid.uuid4())
OTHER_TENANT = str(uuid.uuid4())
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


# ── The stamp ──────────────────────────────────────────────────────────────────────────────


def test_stamping_a_sqlite_session_is_a_no_op(database: Database) -> None:
    """SQLite has no RLS and no ``set_config``; the tests would not run if this raised."""
    with database.tenant_session(TENANT) as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1


class _FakeDialect:
    name = "postgresql"


class _FakeBind:
    dialect = _FakeDialect()


class _RecordingSession:
    """Just enough ``Session`` to see what would reach a Postgres."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def get_bind(self) -> _FakeBind:
        return _FakeBind()

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        self.statements.append((str(statement), params or {}))


def test_stamping_a_postgres_session_sets_the_transaction_local_setting() -> None:
    """The tenant is bound, not interpolated, and the setting is local to the transaction.

    ``is_local => true`` is the whole safety property behind a transaction pooler: the connection
    goes back to the pool without an identity, so the next transaction on it cannot inherit one.
    """
    session = _RecordingSession()

    set_current_tenant(cast(Session, session), TENANT)

    (statement, params) = session.statements[0]
    assert "set_config" in statement
    assert params == {"name": TENANT_SETTING, "value": TENANT}
    assert statement.rstrip().endswith("true)")


# ── The wiring ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _WatchedScope:
    """A ``TenantScope`` that records who each session was opened as, then delegates."""

    database: Database
    seen: list[str]

    @contextmanager
    def __call__(self, tenant_id: str) -> Iterator[Session]:
        self.seen.append(tenant_id)
        with self.database.tenant_session(tenant_id) as session:
            yield session


@pytest.fixture
def scope(database: Database) -> _WatchedScope:
    return _WatchedScope(database=database, seen=[])


def _message(external_id: str = "wamid.1") -> MessageEnvelope:
    return MessageEnvelope(
        external_id=external_id,
        thread_id="966500000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=MessageType.TEXT,
        body_text="hello",
        received_at=NOW,
    )


def _turn() -> InboundTurn:
    return InboundTurn(
        tenant_id=uuid.UUID(TENANT),
        channel="whatsapp",
        channel_thread_id="966500000000",
        channel_identity="+966500000000",
        modality="text",
        text="the shower is leaking",
        received_at=NOW,
        idempotency_key="wamid.1",
    )


def _classification() -> ClassificationResult:
    return ClassificationResult.model_validate(
        {
            "intent": "maintenance_issue",
            "language": "en",
            "summary_one_line": "The shower is leaking",
            "confidence_overall": 0.9,
            "confidence_intent": 0.9,
            "confidence_person": 0.9,
            "confidence_company": 0.9,
        }
    )


def test_the_message_repository_acts_as_the_tenant_it_is_given(scope: _WatchedScope) -> None:
    repository = SessionScopedMessageRepository(scope)

    repository.save(TENANT, _message())
    assert repository.exists(TENANT, "wamid.1") is True

    assert scope.seen == [TENANT, TENANT]


def test_the_loader_acts_as_the_tenant_it_is_given(scope: _WatchedScope) -> None:
    SqlAlchemyMessageLoader(scope).load(TENANT, "wamid.missing")

    assert scope.seen == [TENANT]


def test_the_audit_log_acts_as_the_tenant_on_the_entry(scope: _WatchedScope) -> None:
    SqlAlchemyAuditLog(scope).write(
        AuditEntry(
            tenant_id=TENANT,
            message_id=str(uuid.uuid4()),
            action="needs_review",
            actor="bot",
            classification_snapshot={},
        )
    )

    assert scope.seen == [TENANT]


def test_the_inbox_writer_acts_as_the_tenant_on_the_draft(scope: _WatchedScope) -> None:
    SqlAlchemyInboxWriter(scope).create(
        InboxItemDraft(
            tenant_id=TENANT,
            message_id=str(uuid.uuid4()),
            status=InboxStatus.NEEDS_REVIEW,
            band=ConfidenceBand.LOW,
        )
    )

    assert scope.seen == [TENANT]


def test_the_classification_writer_acts_as_the_tenant_on_the_draft(scope: _WatchedScope) -> None:
    SqlAlchemyClassificationWriter(scope).record(
        ClassificationDraft(
            tenant_id=TENANT,
            message_id=str(uuid.uuid4()),
            result=_classification(),
            model_used="claude-haiku-4-5",
            prompt_version="v3",
            latency_ms=120,
        )
    )

    assert scope.seen == [TENANT]


def test_the_rules_provider_and_crm_lookup_act_as_the_tenant_they_are_given(
    scope: _WatchedScope,
) -> None:
    SqlAlchemyRulesProvider(scope)(TENANT)
    SqlAlchemyCrmLookup(scope)(TENANT, IncomingContact(phone_e164="+966500000000"))

    assert scope.seen == [TENANT, TENANT]


def test_the_clinic_repository_acts_as_the_tenant_on_every_method(scope: _WatchedScope) -> None:
    """The clinic tables are the newest tenant-scoped ones (migration 008) and the easiest to
    reach without a stamp: an import is operator-run, and a read is four joins deep."""
    repository = SqlAlchemyClinicRepository(scope)

    repository.import_catalogue(
        TENANT,
        CataloguePlan(
            branches=(Branch(external_id="B01", name="Riverside"),),
            services=(
                Service(code="DT001", name="Deep Facial", price_minor=75_000, duration_minutes=45),
            ),
        ),
        import_version="rls-test",
    )
    repository.list_branches(TENANT)
    repository.list_services(TENANT)
    repository.list_slots(TENANT)
    repository.list_bookings(TENANT)

    assert scope.seen == [TENANT] * 5


def test_the_conversation_store_acts_as_the_tenant_on_the_turn(scope: _WatchedScope) -> None:
    """Both halves of continuity, including the one whose tenant is two objects deep.

    ``record_reply`` is handed a ``ConversationState``, a turn, a task and an action, and only the
    turn knows whose conversation it is. That is exactly the shape of call that would quietly open
    an unstamped session.
    """
    store = SqlAlchemyConversationStore(scope)
    state = store.begin(_turn())

    store.record_reply(
        state,
        _turn(),
        Task(intent="maintenance_issue", status=TaskStatus.COLLECTING),
        OutboundAction(kind="say", text="Someone is on the way."),
    )

    assert scope.seen == [TENANT, TENANT]


# ── The policies ───────────────────────────────────────────────────────────────────────────

RLS_DATABASE_URL = os.environ.get("WATCHER_RLS_DATABASE_URL")

requires_rls_database = pytest.mark.skipif(
    RLS_DATABASE_URL is None,
    reason=(
        "set WATCHER_RLS_DATABASE_URL to a Postgres with migration 004 applied, connecting as "
        "watcher_app, to run the cross-tenant read test"
    ),
)


@pytest.fixture
def rls_engine() -> Iterator[Engine]:
    assert RLS_DATABASE_URL is not None
    engine = create_db_engine(RLS_DATABASE_URL)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(session: Session, tenant_id: str, body: str) -> None:
    """One tenant and one message belonging to it, written as that tenant.

    Written through the same policies it is about to be read through: an insert that carries a
    ``tenant_id`` other than the session's own is refused by the ``WITH CHECK`` half, which is the
    property being relied on and therefore worth exercising in the setup rather than asserting
    separately.
    """
    set_current_tenant(session, tenant_id)
    session.execute(
        text("INSERT INTO tenants (id, name, tier) VALUES (:id, :name, 'saas')"),
        {"id": tenant_id, "name": f"tenant {tenant_id[:8]}"},
    )
    session.execute(
        text("""
            INSERT INTO messages (
                id, tenant_id, external_id, thread_id, channel, sender_phone_e164,
                direction, type, body_text, received_at, raw_payload, created_at
            ) VALUES (
                gen_random_uuid(), :tenant, :external_id, '966500000000', 'whatsapp',
                '+966500000000', 'inbound', 'text', :body, now(), '{}', now()
            )
        """),
        {"tenant": tenant_id, "external_id": f"wamid.{tenant_id}", "body": body},
    )
    session.commit()


@requires_rls_database
def test_one_tenant_cannot_read_another_tenants_messages(rls_engine: Engine) -> None:
    """The claim AGENTS.md calls non-negotiable, asserted against a real Postgres.

    Note what is *not* in the query: any mention of ``tenant_id``. A bare ``SELECT *`` over
    ``messages`` is the query a bug writes, and the database is what has to answer it safely.
    """
    with Session(rls_engine) as session:
        _seed(session, TENANT, "mine")
        _seed(session, OTHER_TENANT, "not yours")

    try:
        with Session(rls_engine) as session:
            set_current_tenant(session, TENANT)
            bodies = session.execute(text("SELECT body_text FROM messages")).scalars().all()
            assert bodies == ["mine"]

        with Session(rls_engine) as session:
            set_current_tenant(session, OTHER_TENANT)
            bodies = session.execute(text("SELECT body_text FROM messages")).scalars().all()
            assert bodies == ["not yours"]
    finally:
        for tenant_id in (TENANT, OTHER_TENANT):
            with Session(rls_engine) as session:
                set_current_tenant(session, tenant_id)
                session.execute(text("DELETE FROM messages"))
                session.execute(text("DELETE FROM tenants"))
                session.commit()


@requires_rls_database
def test_a_session_with_no_tenant_sees_no_data_but_can_still_resolve_an_endpoint(
    rls_engine: Engine,
) -> None:
    """The one exception, and its boundary.

    An unstamped session is what the tenant resolver runs on, so it must be able to read
    ``channel_configs``. Everything else it asks for has to come back empty — otherwise "no tenant
    set" would be a way to read the whole database rather than a way to ask one question.
    """
    with Session(rls_engine) as session:
        _seed(session, TENANT, "mine")

    try:
        with Session(rls_engine) as session:
            assert session.execute(text("SELECT count(*) FROM messages")).scalar_one() == 0
            assert session.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 0
            # The endpoint lookup, which is the only thing this session exists to do.
            session.execute(text("SELECT tenant_id FROM channel_configs")).scalars().all()
    finally:
        with Session(rls_engine) as session:
            set_current_tenant(session, TENANT)
            session.execute(text("DELETE FROM messages"))
            session.execute(text("DELETE FROM tenants"))
            session.commit()
