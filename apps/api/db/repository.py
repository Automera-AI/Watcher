"""SQLAlchemy implementation of the ingestion ``MessageRepository`` port (addendum §4, §5).

Closes the persistence seam left open in the webhook slice: ``exists``/``save`` against the
``messages`` table, scoped by ``tenant_id``, with the unique ``(tenant_id, wa_message_id)``
constraint providing idempotency at the database level too.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db.models import Message
from apps.api.schemas.message import MessageEnvelope


class SqlAlchemyMessageRepository:
    """Stores raw message envelopes in Postgres/SQLite via a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def exists(self, tenant_id: str, wa_message_id: str) -> bool:
        stmt = select(Message.id).where(
            Message.tenant_id == uuid.UUID(tenant_id),
            Message.wa_message_id == wa_message_id,
        )
        return self._session.execute(stmt).first() is not None

    def save(self, tenant_id: str, message: MessageEnvelope) -> None:
        row = Message(
            tenant_id=uuid.UUID(tenant_id),
            wa_message_id=message.wa_message_id,
            wa_chat_id=message.wa_chat_id,
            sender_phone_e164=message.sender_phone_e164,
            sender_wa_name=message.sender_wa_name,
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
