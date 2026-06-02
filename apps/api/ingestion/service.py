"""Ingestion service — the reliability core of the webhook path (addendum §5).

Per message: dedup on ``wa_message_id`` (Meta re-delivers and can duplicate), then
**persist before enqueue** so a worker crash never loses a message (at-least-once + idempotency
≈ exactly once). The HTTP handler returns 200 quickly *after* this runs, before classification.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.ingestion.ports import ClassificationQueue, MessageRepository
from apps.api.schemas.message import MessageEnvelope


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of ingesting one webhook batch, for logging/metrics."""

    accepted: int
    duplicates: int


class IngestionService:
    """Persists inbound messages and enqueues them for classification."""

    def __init__(self, repository: MessageRepository, queue: ClassificationQueue) -> None:
        self._repository = repository
        self._queue = queue

    def ingest(self, tenant_id: str, messages: list[MessageEnvelope]) -> IngestResult:
        accepted = 0
        duplicates = 0
        for message in messages:
            if self._repository.exists(tenant_id, message.wa_message_id):
                duplicates += 1
                continue
            # Order matters: durable write first, enqueue second (§5).
            self._repository.save(tenant_id, message)
            self._queue.enqueue(tenant_id, message.wa_message_id)
            accepted += 1
        return IngestResult(accepted=accepted, duplicates=duplicates)
