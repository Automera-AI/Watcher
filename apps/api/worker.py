"""The arq worker's composition root (roadmap B5) — the durable half of the queue seam.

Run it, once ``REDIS_URL`` is set::

    arq apps.api.worker.WorkerSettings

``apps/api/main.py`` is the composition root for the process that answers a webhook; this is the
composition root for the process that classifies what it accepted. They stay separate deliberately
— `orchestration/composition.py`'s whole reason to exist is that this file and `main.py` build the
identical ``Orchestrator`` from the identical wiring, so nothing added to one silently goes missing
from the other. ``on_startup``/``on_shutdown`` are arq's hooks for building and releasing that graph
once per worker process, not once per job — the same lifetime `main.py` gives it via ``assemble``.

**Importing this module does nothing that can fail.** ``Settings()`` never raises — every field is
optional (``core/config.py``) — and ``redis_dsn()`` returns ``None`` rather than raising when
``REDIS_URL`` is unset, in which case ``RedisSettings()`` falls back to arq's own default
(``localhost:6379``). Nothing here opens a connection at import time either: ``ArqRedis`` connects
lazily, same as ``build_database``. A test can import this module and call ``consume_message``
directly with a fake ``ctx`` without any of that ever touching a network.

The class-body read below constructs its own ``Settings()`` rather than going through the cached
``get_settings()`` — arq's CLI needs ``WorkerSettings.redis_settings`` set at import time, and this
is one process's one import, but a shared test process importing this module must not leave a
stale value in the process-wide cache for an unrelated test to inherit. ``startup`` below, which
runs once when the worker actually starts, uses ``get_settings()`` as normal.
"""

from __future__ import annotations

import logging
from typing import Any

from arq.connections import RedisSettings

from apps.api.classifier.factory import build_classifier
from apps.api.core.config import Settings, get_settings
from apps.api.db.engine import build_database
from apps.api.orchestration.composition import build_consumer
from apps.api.orchestration.queue import MessageConsumer
from apps.api.orchestration.worker import ProcessOutcome

_logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """Build the object graph once per worker process (not once per job)."""
    settings = get_settings()
    settings.tenant_policy()
    vocabulary = settings.vocabulary()
    database = build_database(settings)
    classifier = build_classifier(settings, vocabulary=vocabulary)
    graph = build_consumer(settings, database, classifier, vocabulary=vocabulary)
    ctx["database"] = database
    ctx["sender"] = graph.sender
    ctx["consumer"] = graph.consumer


async def shutdown(ctx: dict[str, Any]) -> None:
    """Mirror ``main.py``'s ``_shutdown``: close what ``startup`` opened, in the same order."""
    sender = ctx.get("sender")
    if sender is not None:
        sender.close()
    database = ctx.get("database")
    if database is not None:
        database.dispose()


async def consume_message(
    ctx: dict[str, Any], tenant_id: str, external_id: str
) -> ProcessOutcome | None:
    """The job function :class:`~apps.api.orchestration.queue.RedisClassificationQueue` enqueues.

    Reuses ``MessageConsumer.consume`` unmodified — reloads the persisted row and drives it through
    the same ``Orchestrator`` the in-process queue would have, per this module's docstring.
    """
    consumer: MessageConsumer = ctx["consumer"]
    return await consumer.consume(tenant_id, external_id)


_redis_dsn = Settings().redis_dsn()


class WorkerSettings:
    """Read by the ``arq`` CLI. ``redis_settings`` needs no ``REDIS_URL`` to *import* this module —
    see the module docstring — only to actually reach a queue once the worker runs."""

    functions = (consume_message,)
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = (
        RedisSettings.from_dsn(_redis_dsn) if _redis_dsn is not None else RedisSettings()
    )
