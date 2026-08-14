"""In-memory test doubles for the ingestion ports (structurally satisfy the Protocols)."""

from __future__ import annotations

from apps.api.schemas.message import MessageEnvelope


class InMemoryRepository:
    """MessageRepository double; records save order in a shared ``events`` log."""

    def __init__(self, events: list[str] | None = None) -> None:
        self._store: set[tuple[str, str]] = set()
        self.saved: list[tuple[str, MessageEnvelope]] = []
        self.events: list[str] = events if events is not None else []

    def exists(self, tenant_id: str, external_id: str) -> bool:
        return (tenant_id, external_id) in self._store

    def save(self, tenant_id: str, message: MessageEnvelope) -> None:
        self._store.add((tenant_id, message.external_id))
        self.saved.append((tenant_id, message))
        self.events.append(f"save:{message.external_id}")


class RecordingQueue:
    """ClassificationQueue double; records enqueue order in a shared ``events`` log."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.enqueued: list[tuple[str, str]] = []
        self.events: list[str] = events if events is not None else []

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        self.enqueued.append((tenant_id, external_id))
        self.events.append(f"enqueue:{external_id}")
