"""Repository for conversation and task persistence (Item 2.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from packages.intents.schema import Vocabulary, default_vocabulary
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.conversations.task import Task, TaskStatus
from apps.api.db.models import Conversation, TaskRow, Turn
from apps.api.schemas.envelope import InboundTurn


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
