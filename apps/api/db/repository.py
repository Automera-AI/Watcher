"""SQLAlchemy implementation of the ingestion ``MessageRepository`` port (addendum §4, §5).

Closes the persistence seam left open in the webhook slice: ``exists``/``save`` against the
``messages`` table, scoped by ``tenant_id``, with the unique ``(tenant_id, external_id)``
constraint providing idempotency at the database level too.

Two implementations of the same port, and the difference is who owns the session.
:class:`SqlAlchemyMessageRepository` is handed one and uses it — the right shape inside a request
that already has a session, and the shape the tests use. :class:`SessionScopedMessageRepository`
is handed a *scope* and opens one per call, which is what a long-lived object held by the
application can safely do: a session pinned for the process's lifetime would hold one pgbouncer
server connection forever and would accumulate every object it ever loaded.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db.engine import SessionScope
from apps.api.db.models import Message
from apps.api.schemas.message import MessageEnvelope


class SqlAlchemyMessageRepository:
    """Stores raw message envelopes in Postgres/SQLite via a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def exists(self, tenant_id: str, external_id: str) -> bool:
        stmt = select(Message.id).where(
            Message.tenant_id == uuid.UUID(tenant_id),
            Message.external_id == external_id,
        )
        return self._session.execute(stmt).first() is not None

    def save(self, tenant_id: str, message: MessageEnvelope) -> None:
        row = Message(
            tenant_id=uuid.UUID(tenant_id),
            external_id=message.external_id,
            thread_id=message.thread_id,
            channel=message.channel,
            sender_phone_e164=message.sender_phone_e164,
            sender_display_name=message.sender_display_name,
            direction=message.direction.value,
            type=message.type.value,
            body_text=message.body_text,
            media_id=message.media_id,
            media_mime=message.media_mime,
            transcript_text=message.transcript_text,
            received_at=message.received_at,
            raw_payload=message.raw_payload,
        )
        self._session.add(row)
        self._session.commit()


class SessionScopedMessageRepository:
    """The same repository, opening and closing a session per call (the application's copy).

    Delegates rather than reimplements: the SQL that decides what "already stored" means exists
    once, and the two objects cannot answer the same question differently.
    """

    def __init__(self, scope: SessionScope) -> None:
        self._scope = scope

    def exists(self, tenant_id: str, external_id: str) -> bool:
        with self._scope() as session:
            return SqlAlchemyMessageRepository(session).exists(tenant_id, external_id)

    def save(self, tenant_id: str, message: MessageEnvelope) -> None:
        with self._scope() as session:
            SqlAlchemyMessageRepository(session).save(tenant_id, message)
