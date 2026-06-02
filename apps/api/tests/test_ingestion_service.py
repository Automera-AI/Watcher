"""Tests for the ingestion service: idempotency + persist-before-enqueue (addendum §5)."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.api.ingestion.service import IngestionService
from apps.api.schemas.enums import MessageType, SourceKind
from apps.api.schemas.message import MessageEnvelope
from apps.api.tests.fakes import InMemoryRepository, RecordingQueue

TENANT = "tenant-1"


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


def test_accepts_new_messages_and_persists_before_enqueue() -> None:
    events: list[str] = []
    repo = InMemoryRepository(events)
    queue = RecordingQueue(events)
    service = IngestionService(repo, queue)

    result = service.ingest(TENANT, [_message("wamid.A")])

    assert (result.accepted, result.duplicates) == (1, 0)
    assert repo.saved and queue.enqueued
    # Durable write must precede the enqueue (§5).
    assert events == ["save:wamid.A", "enqueue:wamid.A"]


def test_duplicate_message_id_is_skipped() -> None:
    repo = InMemoryRepository()
    queue = RecordingQueue()
    service = IngestionService(repo, queue)

    first = service.ingest(TENANT, [_message("wamid.A")])
    second = service.ingest(TENANT, [_message("wamid.A")])

    assert (first.accepted, first.duplicates) == (1, 0)
    assert (second.accepted, second.duplicates) == (0, 1)
    assert len(repo.saved) == 1
    assert len(queue.enqueued) == 1


def test_same_id_different_tenant_is_not_a_duplicate() -> None:
    repo = InMemoryRepository()
    queue = RecordingQueue()
    service = IngestionService(repo, queue)

    service.ingest("tenant-a", [_message("wamid.A")])
    result = service.ingest("tenant-b", [_message("wamid.A")])

    assert result.accepted == 1  # idempotency is scoped per tenant (§4)
