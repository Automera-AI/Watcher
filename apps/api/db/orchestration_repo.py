"""Database implementations of the orchestration ports (roadmap A4's dependencies).

``Orchestrator`` and ``MessageConsumer`` were written against protocols and had only in-memory
doubles behind them. These are the real ones, and they are what makes the difference between a
process that starts and a process that does something: the loader reloads the persisted row the
queue was given the id of (§5), and the audit log and inbox writer are where a decision stops being
a return value and becomes a record the control page can show (§4, §12).

Every one of them takes a :data:`~apps.api.db.engine.TenantScope` rather than a session. They are
constructed once, at startup, and called from a worker thread per message; a session captured at
construction would be shared across threads, which SQLAlchemy does not support, and would hold a
connection between messages, which the transaction pooler does not appreciate.

That the scope takes a tenant is B2's half of it. Each of these methods was already given the
tenant it was acting for — in an argument, on a draft, on a turn — and passed it no further than
the ``WHERE`` clause. Passing it to the scope instead means the session itself carries it, which is
what the RLS policies in migration ``004`` enforce against, and what makes a forgotten filter a
query that returns nothing rather than a query that returns somebody else's rows.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from enum import Enum

from packages.intents.schema import Vocabulary, default_vocabulary
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.audit.log import AuditEntry
from apps.api.conversations.task import Task, TaskStatus
from apps.api.db.conversation_repo import ConversationRepository, task_from_row, task_to_row
from apps.api.db.engine import TenantScope
from apps.api.db.models import (
    AuditLogRow,
    Classification,
    CrmCacheRow,
    InboxItem,
    Message,
    RuleRow,
    Source,
)
from apps.api.identity.models import CrmRecord
from apps.api.identity.resolver import IncomingContact
from apps.api.orchestration.ports import (
    ClassificationDraft,
    ConversationState,
    InboxItemDraft,
)
from apps.api.orchestration.queue import LoadedMessage
from apps.api.rules.models import Rule
from apps.api.schemas.enums import ConfidenceBand, MessageDirection, MessageType, SourceKind
from apps.api.schemas.envelope import InboundTurn, OutboundAction
from apps.api.schemas.message import MessageEnvelope

_logger = logging.getLogger(__name__)

#: Prior turns handed to the classifier with each message (addendum §7 recommends N=10),
#: oldest→newest, from our own ``messages`` table — history costs a query, never a channel call.
HISTORY_TURNS = 10

#: Cached records offered to identity resolution per message. Dedup is cache-only in v1 (D9-a) and
#: the resolver scores candidates in Python, so this bounds the work per message rather than
#: expressing a product rule. A tenant whose cache outgrows it needs the phone index that a
#: JSON-array column cannot give us — recorded in the spec rather than papered over here.
CANDIDATE_LIMIT = 500


def _envelope(session: Session, row: Message) -> MessageEnvelope:
    """Rebuild the stored envelope from its row.

    ``source_kind`` is not a column on ``messages`` — it is a property of the thread, and it lives
    on ``sources``. Looking it up here keeps the one fact in one place; a thread we have never
    recorded a source row for is a direct conversation, which is what it was before groups existed.
    """
    kind = session.execute(
        select(Source.kind).where(
            Source.tenant_id == row.tenant_id, Source.thread_id == row.thread_id
        )
    ).scalar_one_or_none()
    return MessageEnvelope(
        external_id=row.external_id,
        thread_id=row.thread_id,
        source_kind=SourceKind(kind) if kind is not None else SourceKind.DIRECT,
        sender_phone_e164=row.sender_phone_e164,
        sender_display_name=row.sender_display_name,
        channel=row.channel,
        direction=MessageDirection(row.direction),
        type=MessageType(row.type),
        body_text=row.body_text,
        media_id=row.media_id,
        media_mime=row.media_mime,
        transcript_text=row.transcript_text,
        received_at=row.received_at,
        raw_payload=row.raw_payload,
    )


class SqlAlchemyMessageLoader:
    """Reloads an enqueued message and its recent history (``MessageLoader``, §5 + §7)."""

    def __init__(self, scope: TenantScope, *, history_turns: int = HISTORY_TURNS) -> None:
        self._scope = scope
        self._history_turns = history_turns

    def load(self, tenant_id: str, external_id: str) -> LoadedMessage | None:
        with self._scope(tenant_id) as session:
            row = session.execute(
                select(Message).where(
                    Message.tenant_id == uuid.UUID(tenant_id),
                    Message.external_id == external_id,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return LoadedMessage(
                message_id=str(row.id),
                message=_envelope(session, row),
                history=[_envelope(session, prior) for prior in self._history(session, row)],
            )

    def _history(self, session: Session, row: Message) -> Sequence[Message]:
        """The last N turns in the same thread, oldest→newest.

        Ordered by ``received_at`` rather than by insertion, because a channel that batches or
        retries delivers out of order and the addendum (§7) is explicit that history is assembled
        by message timestamp. The query takes the newest N and the result is then reversed — the
        alternative, taking the oldest N, hands the model the beginning of a long conversation
        instead of the part the message is replying to.
        """
        rows = (
            session.execute(
                select(Message)
                .where(
                    Message.tenant_id == row.tenant_id,
                    Message.thread_id == row.thread_id,
                    Message.id != row.id,
                    Message.received_at <= row.received_at,
                )
                .order_by(Message.received_at.desc())
                .limit(self._history_turns)
            )
            .scalars()
            .all()
        )
        return list(reversed(rows))


class SqlAlchemyAuditLog:
    """Appends to ``audit_log`` — every routing decision, with its classification (§4, §12)."""

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def write(self, entry: AuditEntry) -> None:
        with self._scope(entry.tenant_id) as session:
            session.add(
                AuditLogRow(
                    tenant_id=uuid.UUID(entry.tenant_id),
                    message_id=uuid.UUID(entry.message_id),
                    action=entry.action,
                    actor=entry.actor,
                    classification_snapshot=entry.classification_snapshot,
                    destination_id=_optional_uuid(entry.destination_id),
                    destination_record_id=entry.destination_record_id,
                    destination_record_url=entry.destination_record_url,
                )
            )


class SqlAlchemyClassificationWriter:
    """Writes the ``classifications`` row for one message (``ClassificationWriter``, §4).

    This table has existed since the data model landed and nothing has ever written to it. The
    reason it stayed empty is that two of its columns — ``latency_ms`` and ``prompt_version`` —
    could only be answered by the classifier service, which did not report them; A5 made the
    service report them rather than filling the columns with something plausible from here.

    The returned id is what the inbox item points at, so the control page can show *why* a message
    was filed the way it was without the snapshot being the only copy of it.
    """

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def record(self, draft: ClassificationDraft) -> str | None:
        result = draft.result
        with self._scope(draft.tenant_id) as session:
            row = Classification(
                tenant_id=uuid.UUID(draft.tenant_id),
                message_id=uuid.UUID(draft.message_id),
                intent=_enum_value(result.intent),
                person_name=result.person_name,
                person_appears_to_be=result.person_appears_to_be,
                company_name=result.company_name,
                company_domain_hint=result.company_domain_hint,
                phone_e164=result.phone_e164,
                language=_enum_value(result.language),
                summary_one_line=result.summary_one_line,
                suggested_record_type=_optional_enum_value(result.suggested_record_type),
                confidence_overall=result.confidence_overall,
                confidence_intent=result.confidence_intent,
                confidence_person=result.confidence_person,
                confidence_company=result.confidence_company,
                model_used=draft.model_used,
                prompt_version=draft.prompt_version,
                latency_ms=draft.latency_ms,
            )
            session.add(row)
            session.flush()
            return str(row.id)


class SqlAlchemyInboxWriter:
    """Creates the ``inbox_items`` row the control page triages from (§4, §12)."""

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def create(self, draft: InboxItemDraft) -> None:
        with self._scope(draft.tenant_id) as session:
            session.add(
                InboxItem(
                    tenant_id=uuid.UUID(draft.tenant_id),
                    message_id=uuid.UUID(draft.message_id),
                    status=draft.status.value,
                    # `band` is nullable on the draft and not on the row. A draft without a band
                    # is the unclassified path, and a message the model could not read is by
                    # definition not confident enough to act on — which is what LOW means.
                    band=(draft.band or ConfidenceBand.LOW).value,
                    classification_id=_optional_uuid(draft.classification_id),
                )
            )


class SqlAlchemyRulesProvider:
    """A tenant's enabled auto-routing rules, in priority order (``RulesProvider``, §12)."""

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def __call__(self, tenant_id: str) -> list[Rule]:
        with self._scope(tenant_id) as session:
            rows = (
                session.execute(
                    select(RuleRow)
                    .where(RuleRow.tenant_id == uuid.UUID(tenant_id), RuleRow.enabled.is_(True))
                    .order_by(RuleRow.priority)
                )
                .scalars()
                .all()
            )
        return [rule for rule in (_rule_from(row) for row in rows) if rule is not None]


class SqlAlchemyConversationStore:
    """Conversation, turn and task continuity across messages (``ConversationStore``, §4).

    ``ConversationRepository`` was written in item 2.1 and called by nothing outside its own tests
    for four sessions. This is the adapter that puts it in the message path: what the guest said is
    recorded, the job already in flight is loaded, and after the receptionist has spoken both the
    updated job and the reply are written back.

    Each call takes its own session, like every other adapter here — these objects are built once
    and used from a worker thread per message.
    """

    def __init__(self, scope: TenantScope, *, vocabulary: Vocabulary | None = None) -> None:
        self._scope = scope
        self._vocabulary = vocabulary or default_vocabulary()

    def begin(self, turn: InboundTurn) -> ConversationState:
        with self._scope(str(turn.tenant_id)) as session:
            repo = ConversationRepository(session)
            conversation = repo.find_or_create_conversation(
                str(turn.tenant_id), turn.channel, turn.channel_thread_id
            )
            repo.record_turn(conversation.id, turn)
            conversation.last_turn_at = turn.received_at

            row = repo.get_active_task(conversation.id)
            return ConversationState(
                conversation_id=str(conversation.id),
                task=(task_from_row(row, vocabulary=self._vocabulary) if row is not None else None),
                # Only replies sent in service of the job now in flight count against its
                # clarifying-turn budget; see `count_outbound_turns`.
                replies_sent=repo.count_outbound_turns(
                    conversation.id, since=row.created_at if row is not None else None
                ),
            )

    def record_reply(
        self,
        state: ConversationState,
        turn: InboundTurn,
        task: Task | None,
        action: OutboundAction,
    ) -> None:
        conversation_id = uuid.UUID(state.conversation_id)
        with self._scope(str(turn.tenant_id)) as session:
            repo = ConversationRepository(session)

            if task is None:
                # An emergency reply (G3): it belongs to the transcript and to no job. The active
                # task is deliberately untouched — see the port's docstring.
                repo.record_outbound_turn(conversation_id, turn, action)
                return

            row = repo.get_active_task(conversation_id)

            if row is not None and row.intent != task.intent:
                # The guest asked about something else. The old job did not fail and was not
                # finished; it was left, and it has to leave the active set or every later message
                # resumes a conversation nobody is having.
                row.status = TaskStatus.ABANDONED.value
                repo.save_task(row)
                row = None

            if row is None:
                row = repo.create_task(conversation_id, str(turn.tenant_id), task.intent)

            repo.save_task(task_to_row(task, row))
            repo.record_outbound_turn(conversation_id, turn, action)


class SqlAlchemyCrmLookup:
    """Cached destination records to dedup an incoming contact against (``CrmLookup``, §9)."""

    def __init__(self, scope: TenantScope, *, limit: int = CANDIDATE_LIMIT) -> None:
        self._scope = scope
        self._limit = limit

    def __call__(self, tenant_id: str, contact: IncomingContact) -> list[CrmRecord]:
        with self._scope(tenant_id) as session:
            rows = (
                session.execute(
                    select(CrmCacheRow)
                    .where(CrmCacheRow.tenant_id == uuid.UUID(tenant_id))
                    .order_by(CrmCacheRow.last_synced_at.desc().nullslast())
                    .limit(self._limit)
                )
                .scalars()
                .all()
            )
        return [
            CrmRecord(
                external_record_id=row.external_record_id,
                name=row.name,
                company=row.company,
                phones=list(row.phones),
            )
            for row in rows
        ]


def _rule_from(row: RuleRow) -> Rule | None:
    """Validate one stored rule, or drop it with a warning.

    Conditions and actions are jsonb written by the control page, so a row can be structurally
    wrong in a way the column type cannot catch. Raising here would take the whole tenant's
    classification down for one bad rule; the message is still routed by confidence band, and the
    broken rule is named in the log rather than silently obeyed.
    """
    try:
        # `model_validate` rather than the constructor: the jsonb columns are `dict[str, Any]` on
        # the way out of the database, and the condition union is exactly what is being checked.
        return Rule.model_validate(
            {
                "id": str(row.id),
                "name": row.name,
                "conditions": row.conditions,
                "action": row.action,
                "enabled": row.enabled,
                "priority": row.priority,
            }
        )
    except ValidationError:
        _logger.warning("skipping unparseable rule %s for tenant %s", row.id, row.tenant_id)
        return None


def _optional_uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value is not None else None


def _enum_value(value: object) -> str:
    """The stored form of a taxonomy value, whether it arrives as an enum or already as a string."""
    return str(value.value) if isinstance(value, Enum) else str(value)


def _optional_enum_value(value: object | None) -> str | None:
    return None if value is None else _enum_value(value)
