"""Tests for the SQLAlchemy message repository on in-memory SQLite (addendum §4, §5)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.db.base import Base
from apps.api.db.repository import SqlAlchemyMessageRepository
from apps.api.ingestion.service import IngestionService
from apps.api.schemas.enums import MessageType, SourceKind
from apps.api.schemas.message import MessageEnvelope
from apps.api.tests.fakes import RecordingQueue

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _message(wa_message_id: str) -> MessageEnvelope:
    return MessageEnvelope(
        wa_message_id=wa_message_id,
        wa_chat_id="966500000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=MessageType.TEXT,
        body_text="hello",
        received_at=datetime.now(UTC),
    )


def test_save_then_exists(session: Session) -> None:
    repo = SqlAlchemyMessageRepository(session)
    assert repo.exists(TENANT_A, "wamid.A") is False
    repo.save(TENANT_A, _message("wamid.A"))
    assert repo.exists(TENANT_A, "wamid.A") is True


def test_exists_is_tenant_scoped(session: Session) -> None:
    repo = SqlAlchemyMessageRepository(session)
    repo.save(TENANT_A, _message("wamid.A"))
    assert repo.exists(TENANT_B, "wamid.A") is False  # same id, different tenant (§4)


def test_repository_drives_ingestion_dedup(session: Session) -> None:
    repo = SqlAlchemyMessageRepository(session)
    service = IngestionService(repo, RecordingQueue())

    first = service.ingest(TENANT_A, [_message("wamid.A")])
    second = service.ingest(TENANT_A, [_message("wamid.A")])

    assert (first.accepted, first.duplicates) == (1, 0)
    assert (second.accepted, second.duplicates) == (0, 1)


def test_persisted_fields_round_trip(session: Session) -> None:
    repo = SqlAlchemyMessageRepository(session)
    repo.save(TENANT_A, _message("wamid.A"))

    from apps.api.db.models import Message

    row = session.query(Message).filter_by(wa_message_id="wamid.A").one()
    assert row.sender_phone_e164 == "+966500000000"
    assert row.type == MessageType.TEXT.value
    assert row.body_text == "hello"
