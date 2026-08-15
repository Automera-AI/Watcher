"""Queue/worker wiring — the consumer half of §5 (reload the persisted message, then orchestrate).

Ingestion persists a message and enqueues only its id (``ingestion.ports.ClassificationQueue``).
This module is the other side: the consumer **reloads the durable row** — so a crash between persist
and process loses nothing (§5) — and runs it through the :class:`Orchestrator`. One
:class:`MessageConsumer` is shared by two transports:

* :class:`BackgroundTasksQueue` — FastAPI ``BackgroundTasks``, the "now" path: the webhook returns
  200 before classification runs (§5). It is bound to a single request's ``BackgroundTasks``.
* :class:`InlineClassificationQueue` — runs the consumer synchronously; for single-process dev and
  for wiring ``create_app`` without a live request.
* :class:`ThreadPoolClassificationQueue` — the same fast-200 behaviour for a queue that is built
  once and lives as long as the process, which is what the composition root (A4) needs.

All three satisfy the existing ``ClassificationQueue`` seam, so ingestion is unchanged. The durable
swap — an arq/Redis worker for multi-process scale — calls the same ``MessageConsumer.consume`` on
a worker, so nothing else changes.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
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

    def load(self, tenant_id: str, external_id: str) -> LoadedMessage | None: ...


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

    def consume(self, tenant_id: str, external_id: str) -> ProcessOutcome | None:
        """Process one enqueued message; returns ``None`` (and logs) if the row is missing."""
        loaded = self._loader.load(tenant_id, external_id)
        if loaded is None:
            self._logger.warning(
                "enqueued message not found: tenant=%s external_id=%s", tenant_id, external_id
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

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        self._background_tasks.add_task(self._consumer.consume, tenant_id, external_id)


class InlineClassificationQueue:
    """``ClassificationQueue`` consuming synchronously — single-process dev / scripted wiring."""

    def __init__(self, consumer: MessageConsumer) -> None:
        self._consumer = consumer

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        self._consumer.consume(tenant_id, external_id)


#: Concurrent classifications in one process. Each one is mostly waiting on a model, so this is a
#: cap on in-flight work rather than on CPU; small enough that a burst queues instead of opening a
#: connection per message, since ``NullPool`` gives every session its own.
DEFAULT_QUEUE_WORKERS = 4


class ThreadPoolClassificationQueue:
    """``ClassificationQueue`` on a small thread pool — the process-level fast-200 path.

    ``create_app`` is handed one queue for the lifetime of the application, and
    ``BackgroundTasksQueue`` cannot be that queue: it is bound to a single request's
    ``BackgroundTasks``, which only exists once a request is in flight. Consuming inline instead
    would make the webhook wait for two model calls before answering, and the platform retries a
    slow webhook — turning one guest's message into several (§5 is explicit that 200 comes before
    classification). So the composition root gets a pool, and the request thread's involvement
    ends at ``submit``.

    In-flight work is lost on restart. That is the honest cost of an in-process queue and it is
    what B5 replaces with arq/Redis, against this same seam; persistence-before-enqueue means the
    row survives, so a lost message is a message not yet classified, not a message not received.
    """

    def __init__(
        self,
        consumer: MessageConsumer,
        *,
        max_workers: int = DEFAULT_QUEUE_WORKERS,
        logger: logging.Logger = _logger,
    ) -> None:
        self._consumer = consumer
        self._logger = logger
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="classify")

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        self._executor.submit(self._consume, tenant_id, external_id)

    def _consume(self, tenant_id: str, external_id: str) -> None:
        """Consume one message, logging anything that escapes.

        A future whose exception is never retrieved is a silent failure: the message is simply
        never classified and nothing anywhere says so. Nothing retrieves these futures — the
        webhook has long since answered — so the logging has to happen in the worker.
        """
        try:
            self._consumer.consume(tenant_id, external_id)
        except Exception:
            self._logger.exception(
                "classification failed: tenant=%s external_id=%s", tenant_id, external_id
            )

    def shutdown(self, *, wait: bool = True) -> None:
        """Drain in-flight work. Called from the application's shutdown handler."""
        self._executor.shutdown(wait=wait)
