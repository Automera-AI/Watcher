"""Ports for ingestion — the interfaces the service depends on (addendum §5).

The real implementations (Postgres repository, FastAPI BackgroundTasks → arq/Redis queue) land in
their own slices; defining the seams here keeps the service unit-testable with in-memory doubles and
keeps the persistence choice out of the ingestion logic.
"""

from __future__ import annotations

from typing import Protocol

from apps.api.schemas.message import MessageEnvelope


class MessageRepository(Protocol):
    """Durable storage for raw inbound messages, scoped to a tenant."""

    def exists(self, tenant_id: str, wa_message_id: str) -> bool:
        """True if this message was already stored (idempotency on ``wa_message_id``, §5)."""
        ...

    def save(self, tenant_id: str, message: MessageEnvelope) -> None:
        """Persist the raw envelope, *before* enqueue, so a crash never loses a message (§5)."""
        ...


class ClassificationQueue(Protocol):
    """Hand a stored message off for asynchronous classification."""

    def enqueue(self, tenant_id: str, wa_message_id: str) -> None:
        """Enqueue by id only; the worker reloads the persisted row (§5)."""
        ...
