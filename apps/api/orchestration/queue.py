"""Queue/worker wiring — the consumer half of §5 (reload the persisted message, then orchestrate).

Ingestion persists a message and enqueues only its id (``ingestion.ports.ClassificationQueue``).
This module is the other side: the consumer **reloads the durable row** — so a crash between persist
and process loses nothing (§5) — and runs it through the :class:`Orchestrator`. One
:class:`MessageConsumer` is shared by two transports:

* :class:`BackgroundTasksQueue` — FastAPI ``BackgroundTasks``, the "now" path: the webhook returns
  200 before classification runs (§5). It is bound to a single request's ``BackgroundTasks``.
* :class:`InlineClassificationQueue` — runs the consumer synchronously; for single-process dev and
  for wiring ``create_app`` without a live request.

Both satisfy the existing ``ClassificationQueue`` seam, so ingestion is unchanged. The durable swap
— an arq/Redis worker for multi-process scale — calls the same ``MessageConsumer.consume`` on a
worker, so nothing else changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import BackgroundTasks

from apps.api.orchestration.worker import Orchestrator, ProcessOutcome
from apps.api.schemas.message import MessageEnvelope

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoadedMessage:
    """A reloaded message ready for orchestration: its persistent id, envelope, and prior turns."""

    message_id: str
    message: MessageEnvelope
    history: list[MessageEnvelope] = field(default_factory=list)


class MessageLoader(Protocol):
    """Reloads a persisted message (and its history) by the ingest key. ``None`` if it's gone."""

    def load(self, tenant_id: str, wa_message_id: str) -> LoadedMessage | None: ...


class MessageConsumer:
    """Reloads an enqueued message and runs it through the orchestrator (the worker body, §5)."""

    def __init__(
        self,
        loader: MessageLoader,
        orchestrator: Orchestrator,
        *,
        logger: logging.Logger = _logger,
    ) -> None:
        self._loader = loader
        self._orchestrator = orchestrator
        self._logger = logger

    def consume(self, tenant_id: str, wa_message_id: str) -> ProcessOutcome | None:
        """Process one enqueued message; returns ``None`` (and logs) if the row is missing."""
        loaded = self._loader.load(tenant_id, wa_message_id)
        if loaded is None:
            # Enqueued but not loadable — a bug or a lost write; never silently drop it.
            self._logger.warning(
                "enqueued message not found: tenant=%s wa_message_id=%s", tenant_id, wa_message_id
            )
            return None
        return self._orchestrator.process(
            tenant_id, loaded.message_id, loaded.message, loaded.history
        )


class BackgroundTasksQueue:
    """``ClassificationQueue`` backed by one request's FastAPI ``BackgroundTasks`` (now path)."""

    def __init__(self, consumer: MessageConsumer, background_tasks: BackgroundTasks) -> None:
        self._consumer = consumer
        self._background_tasks = background_tasks

    def enqueue(self, tenant_id: str, wa_message_id: str) -> None:
        # Runs after the 200 is sent, so Meta isn't kept waiting on the LLM (§5).
        self._background_tasks.add_task(self._consumer.consume, tenant_id, wa_message_id)


class InlineClassificationQueue:
    """``ClassificationQueue`` consuming synchronously — single-process dev / scripted wiring."""

    def __init__(self, consumer: MessageConsumer) -> None:
        self._consumer = consumer

    def enqueue(self, tenant_id: str, wa_message_id: str) -> None:
        self._consumer.consume(tenant_id, wa_message_id)
