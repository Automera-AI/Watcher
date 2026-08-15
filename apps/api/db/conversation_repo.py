"""Repository for conversation and task persistence (Item 2.1).

Written in item 2.1 and called by nothing but its own tests until roadmap A5 wired the
orchestrator to it. The additions A5 needed are all about the *next* turn: recording what we
said, counting how often we have said it, and finding an inbound turn we have already seen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from packages.intents.schema import Vocabulary, default_vocabulary
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.conversations.task import Task, TaskStatus
from apps.api.db.models import Conversation, TaskRow, Turn
from apps.api.schemas.envelope import InboundTurn, OutboundAction

#: Appended to an inbound turn's idempotency key to make the reply's. Deterministic on purpose —
#: see :meth:`ConversationRepository.record_outbound_turn`.
OUTBOUND_KEY_SUFFIX = ":reply"


class ConversationRepository:
    """Manages conversation, turn, and task persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_or_create_conversation(
        self,
        tenant_id: str,
        channel: str,
        channel_thread_id: str,
    ) -> Conversation:
        tid = uuid.UUID(tenant_id)
        stmt = select(Conversation).where(
            Conversation.tenant_id == tid,
            Conversation.channel_thread_id == channel_thread_id,
            Conversation.status == "open",
        )
        conv = self._session.execute(stmt).scalars().first()
        if conv is not None:
            return conv
        conv = Conversation(
            tenant_id=tid,
            channel=channel,
            channel_thread_id=channel_thread_id,
            started_at=datetime.now(UTC),
        )
        self._session.add(conv)
        self._session.flush()
        return conv

    def record_turn(self, conversation_id: uuid.UUID, turn: InboundTurn) -> Turn:
        """Record what the guest said, once.

        ``turns.idempotency_key`` is unique, and the key is the channel's own id for the message.
        A redelivery that gets as far as here — a queue retry after a partial failure, say — must
        find the existing row rather than raise, because the alternative is a message that can
        never be processed at all.
        """
        existing = (
            self._session.execute(select(Turn).where(Turn.idempotency_key == turn.idempotency_key))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        row = Turn(
            conversation_id=conversation_id,
            tenant_id=turn.tenant_id,
            direction="inbound",
            channel=turn.channel,
            modality=turn.modality,
            body_text=turn.text,
            speech_confidence=turn.speech_confidence,
            idempotency_key=turn.idempotency_key,
            raw_payload=turn.raw,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def record_outbound_turn(
        self,
        conversation_id: uuid.UUID,
        turn: InboundTurn,
        action: OutboundAction,
    ) -> Turn:
        """Record what we said back, keyed off the turn it answers.

        The key is derived from the inbound one rather than generated, so re-processing the same
        message cannot leave two replies in the transcript. ``raw_payload`` carries the action's
        kind: the transcript is what the next turn's continuity is read from, and "we asked" and
        "we confirmed" are different facts about the same text.
        """
        key = f"{turn.idempotency_key}{OUTBOUND_KEY_SUFFIX}"
        existing = (
            self._session.execute(select(Turn).where(Turn.idempotency_key == key)).scalars().first()
        )
        if existing is not None:
            return existing
        row = Turn(
            conversation_id=conversation_id,
            tenant_id=turn.tenant_id,
            direction="outbound",
            channel=turn.channel,
            modality="text",
            body_text=action.text,
            idempotency_key=key,
            raw_payload={"kind": action.kind, "quick_replies": action.quick_replies or []},
        )
        self._session.add(row)
        self._session.flush()
        return row

    def count_outbound_turns(
        self, conversation_id: uuid.UUID, *, since: datetime | None = None
    ) -> int:
        """How many replies we have already sent, optionally only since a moment.

        ``since`` is the active task's creation time, which is what makes this a count of turns
        spent on *this* job rather than on the whole conversation. A guest who books a cleaning
        and then asks about parking starts the clarifying-turn budget again, because they are
        asking about something else.
        """
        stmt = (
            select(func.count())
            .select_from(Turn)
            .where(
                Turn.conversation_id == conversation_id,
                Turn.direction == "outbound",
            )
        )
        if since is not None:
            stmt = stmt.where(Turn.created_at >= since)
        return int(self._session.execute(stmt).scalar_one())

    def get_active_task(self, conversation_id: uuid.UUID) -> TaskRow | None:
        stmt = select(TaskRow).where(
            TaskRow.conversation_id == conversation_id,
            TaskRow.status.in_(["collecting", "ready", "executing"]),
        )
        return self._session.execute(stmt).scalars().first()

    def save_task(self, task_row: TaskRow) -> None:
        task_row.updated_at = datetime.now(UTC)
        self._session.add(task_row)
        self._session.flush()

    def create_task(
        self,
        conversation_id: uuid.UUID,
        tenant_id: str,
        intent: str,
    ) -> TaskRow:
        row = TaskRow(
            conversation_id=conversation_id,
            tenant_id=uuid.UUID(tenant_id),
            intent=intent,
        )
        self._session.add(row)
        self._session.flush()
        return row


def task_from_row(row: TaskRow, vocabulary: Vocabulary | None = None) -> Task:
    """Reconstitute an in-memory Task from a persisted TaskRow."""
    vocab = vocabulary or default_vocabulary()
    return Task(
        intent=row.intent,
        slots=dict(row.slots),
        confirmed=set(row.slots_confirmed),
        status=TaskStatus(row.status),
        vocabulary=vocab,
    )


def task_to_row(task: Task, row: TaskRow) -> TaskRow:
    """Sync in-memory Task state back to a TaskRow for persistence."""
    row.intent = task.intent
    row.slots = dict(task.slots)
    row.slots_confirmed = list(task.confirmed)
    row.status = task.status.value
    return row
