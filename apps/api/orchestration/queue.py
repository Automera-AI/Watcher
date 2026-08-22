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
  once and lives as long as the process, which is what the composition root (A4) needs when no
  durable broker is configured.
* :class:`RedisClassificationQueue` — the durable swap (roadmap B5): the same fast-200 behaviour,
  but the hand-off survives a restart because it lands in Redis instead of process memory. Consumed
  by an arq worker in its own process (``apps/api/worker.py``) that calls the exact same
  ``MessageConsumer.consume`` the other two transports call directly — the seam this module exists
  for is precisely what let B5 add a durable transport without touching ingestion at all.

All four satisfy the existing ``ClassificationQueue`` seam (``enqueue`` stays synchronous), so
ingestion is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

from arq.connections import ArqRedis
from fastapi import BackgroundTasks
from redis.asyncio import ConnectionPool as RedisConnectionPool

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

    async def consume(self, tenant_id: str, external_id: str) -> ProcessOutcome | None:
        """Process one enqueued message; returns ``None`` (and logs) if the row is missing.

        Asynchronous since A5: the orchestrator awaits a receptionist and a channel send. Each
        transport below owns the decision of *where* that loop comes from, which is the whole
        reason the loop is not opened inside the orchestrator itself.
        """
        loaded = self._loader.load(tenant_id, external_id)
        if loaded is None:
            self._logger.warning(
                "enqueued message not found: tenant=%s external_id=%s", tenant_id, external_id
            )
            return None
        return await self._orchestrator.process(
            tenant_id, loaded.message_id, loaded.message, loaded.history
        )


class BackgroundTasksQueue:
    """``ClassificationQueue`` backed by one request's FastAPI ``BackgroundTasks`` (now path).

    ``add_task`` accepts a coroutine function and awaits it on the server's own loop after the
    response is sent, so the async consumer needs no loop of its own here.
    """

    def __init__(self, consumer: MessageConsumer, background_tasks: BackgroundTasks) -> None:
        self._consumer = consumer
        self._background_tasks = background_tasks

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        self._background_tasks.add_task(self._consumer.consume, tenant_id, external_id)


class InlineClassificationQueue:
    """``ClassificationQueue`` consuming synchronously — single-process dev / scripted wiring.

    ``asyncio.run`` means this cannot be called from inside a running event loop, which is a
    correct restriction rather than a limitation: consuming inline from a request handler is what
    makes the webhook wait for the model, and §5 forbids exactly that.
    """

    def __init__(self, consumer: MessageConsumer) -> None:
        self._consumer = consumer

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        asyncio.run(self._consumer.consume(tenant_id, external_id))


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

        The event loop is opened here, one per message, on the thread that will do the work. That
        is the cost of running an async pipeline from a thread pool, and it is small next to the
        model call it wraps; B5's worker replaces the pool and keeps a loop per process instead.
        """
        try:
            asyncio.run(self._consumer.consume(tenant_id, external_id))
        except Exception:
            self._logger.exception(
                "classification failed: tenant=%s external_id=%s", tenant_id, external_id
            )

    def shutdown(self, *, wait: bool = True) -> None:
        """Drain in-flight work. Called from the application's shutdown handler."""
        self._executor.shutdown(wait=wait)


def build_redis_pool(dsn: str) -> ArqRedis:
    """A connection pool for the durable queue (B5), built without connecting.

    Mirrors ``db/engine.py``: constructing a pool touches no network — ``redis-py`` connects
    lazily on the first command — so a bad ``REDIS_URL`` or an unreachable host surfaces on the
    first push, not at startup. That is the same trade already made for ``DATABASE_URL``.
    """
    return ArqRedis(pool_or_conn=RedisConnectionPool.from_url(dsn))


class RedisClassificationQueue:
    """``ClassificationQueue`` backed by arq/Redis (B5) — the hand-off survives a restart.

    ``enqueue`` stays synchronous, like every other transport in this module, so ingestion does not
    change: the Redis push runs as a background task on the caller's already-running loop, which
    always exists here because the webhook handler is ``async def``. It is fire-and-forget rather
    than awaited inline for the same reason :class:`ThreadPoolClassificationQueue` does not block
    on classification — a slow round trip in the request path is one more thing the platform can
    time out and retry — and the risk that leaves (a process dying in the few milliseconds between
    scheduling the task and Redis acknowledging it) is no larger than the window
    :class:`BackgroundTasksQueue` already accepts today: the persisted row survives either way
    (§5); only its classification would need retriggering, by hand, which is exactly the gap B5
    exists to shrink, not the one it claims to close.

    A future whose exception nothing retrieves is a silent failure (see
    :class:`ThreadPoolClassificationQueue._consume`); the same discipline applies here, so pushes
    are logged rather than left to vanish, and pending tasks are tracked so shutdown can drain them.
    """

    def __init__(self, pool: ArqRedis, *, logger: logging.Logger = _logger) -> None:
        self._pool = pool
        self._logger = logger
        self._pending: set[asyncio.Task[None]] = set()

    def enqueue(self, tenant_id: str, external_id: str) -> None:
        task = asyncio.create_task(self._push(tenant_id, external_id))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _push(self, tenant_id: str, external_id: str) -> None:
        try:
            await self._pool.enqueue_job("consume_message", tenant_id, external_id)
        except Exception:
            self._logger.exception(
                "failed to enqueue onto redis: tenant=%s external_id=%s", tenant_id, external_id
            )

    async def aclose(self) -> None:
        """Drain in-flight pushes, then release the connection pool (application shutdown)."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        await self._pool.aclose()
