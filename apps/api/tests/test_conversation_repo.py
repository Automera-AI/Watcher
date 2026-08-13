"""Tests for the conversation repository (Item 2.1)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.db.base import Base
from apps.api.db.conversation_repo import (
    ConversationRepository,
    task_from_row,
    task_to_row,
)
from apps.api.schemas.envelope import InboundTurn

TENANT = str(uuid.uuid4())


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _turn() -> InboundTurn:
    return InboundTurn(
        tenant_id=uuid.UUID(TENANT),
        channel="whatsapp",
        channel_thread_id="thread-1",
        channel_identity="+966500000000",
        modality="text",
        text="hello",
        received_at=datetime.now(UTC),
        idempotency_key="key-1",
    )


def test_find_or_create_conversation_creates_new(session: Session) -> None:
    repo = ConversationRepository(session)
    conv = repo.find_or_create_conversation(TENANT, "whatsapp", "thread-1")
    assert conv.id is not None
    assert conv.status == "open"
    assert conv.channel == "whatsapp"


def test_find_or_create_returns_existing(session: Session) -> None:
    repo = ConversationRepository(session)
    first = repo.find_or_create_conversation(TENANT, "whatsapp", "thread-1")
    second = repo.find_or_create_conversation(TENANT, "whatsapp", "thread-1")
    assert first.id == second.id


def test_record_turn(session: Session) -> None:
    repo = ConversationRepository(session)
    conv = repo.find_or_create_conversation(TENANT, "whatsapp", "thread-1")
    turn = repo.record_turn(conv.id, _turn())
    assert turn.conversation_id == conv.id
    assert turn.body_text == "hello"
    assert turn.direction == "inbound"


def test_create_and_get_active_task(session: Session) -> None:
    repo = ConversationRepository(session)
    conv = repo.find_or_create_conversation(TENANT, "whatsapp", "thread-1")
    repo.create_task(conv.id, TENANT, "booking_enquiry")
    task = repo.get_active_task(conv.id)
    assert task is not None
    assert task.intent == "booking_enquiry"
    assert task.status == "collecting"


def test_get_active_task_returns_none_when_no_task(session: Session) -> None:
    repo = ConversationRepository(session)
    conv = repo.find_or_create_conversation(TENANT, "whatsapp", "thread-1")
    assert repo.get_active_task(conv.id) is None


def test_task_round_trip_via_row(session: Session) -> None:
    repo = ConversationRepository(session)
    conv = repo.find_or_create_conversation(TENANT, "whatsapp", "thread-1")
    row = repo.create_task(conv.id, TENANT, "booking_enquiry")

    task = task_from_row(row)
    task.absorb({"date": "2026-01-15"})
    task.confirmed.add("date")
    task_to_row(task, row)
    repo.save_task(row)

    reloaded = repo.get_active_task(conv.id)
    assert reloaded is not None
    reconstituted = task_from_row(reloaded)
    assert reconstituted.slots == {"date": "2026-01-15"}
    assert "date" in reconstituted.confirmed
