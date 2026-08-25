"""In-memory test doubles for the ingestion ports (structurally satisfy the Protocols)."""

from __future__ import annotations

from typing import Any

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


class RecordingUnclaimedDeliveryStore:
    """UnclaimedDeliveryStore double that retains complete payloads."""

    def __init__(self) -> None:
        self.saved: list[tuple[str | None, dict[str, Any], str]] = []

    def save(self, endpoint_id: str | None, payload: dict[str, Any], reason: str) -> None:
        self.saved.append((endpoint_id, payload, reason))
