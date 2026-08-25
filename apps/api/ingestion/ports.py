"""Ports for ingestion — the interfaces the service depends on (addendum §5).

The real implementations (Postgres repository, FastAPI BackgroundTasks → arq/Redis queue) land in
their own slices; defining the seams here keeps the service unit-testable with in-memory doubles and
keeps the persistence choice out of the ingestion logic.
"""

from __future__ import annotations

from typing import Any, Protocol

from apps.api.schemas.message import MessageEnvelope


class UnknownEndpoint(LookupError):
    """No enabled configuration matches the endpoint a message arrived at.

    Part of the ``TenantResolver`` contract rather than the resolver implementation's own business,
    because the webhook route has to be able to *tell this apart* from a transient failure and it
    cannot import the persistence layer to do it. The distinction is the whole point: a database
    that is momentarily unreachable is worth a retry, and an endpoint nobody has configured is not
    — no number of redeliveries writes the missing row.
    """


class UnclaimedDeliveryStore(Protocol):
    """Durable storage for webhook changes that cannot yet be assigned to a tenant."""

    def save(self, endpoint_id: str | None, payload: dict[str, Any], reason: str) -> None:
        """Persist the complete change before Meta is acknowledged."""
        ...


class MessageRepository(Protocol):
    """Durable storage for raw inbound messages, scoped to a tenant."""

    def exists(self, tenant_id: str, external_id: str) -> bool:
        """True if this message was already stored (idempotency on ``external_id``, §5)."""
        ...

    def save(self, tenant_id: str, message: MessageEnvelope) -> None:
        """Persist the raw envelope, *before* enqueue, so a crash never loses a message (§5)."""
        ...


class ClassificationQueue(Protocol):
    """Hand a stored message off for asynchronous classification."""

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        """Enqueue by id only; the worker reloads the persisted row (§5)."""
        ...
