"""The composition root — the first production caller of ``create_app`` (roadmap A4).

Every other module in this tree takes its collaborators as arguments and knows nothing about where
they come from. That was deliberate, and it left exactly one thing missing: somewhere that *does*
know, that reads the configuration once and builds the object graph the running process is. This is
that place, and it is the only place. Nothing else calls ``get_settings()``, opens an engine, or
decides which classifier is in use.

Run it::

    uvicorn apps.api.main:create_application --factory --host 0.0.0.0 --port 8000

``--factory`` rather than a module-level ``app`` on purpose. A module-level application is built at
*import* time, which would mean importing this module reads the environment, opens an engine, and
fails on a machine that has neither — including when a linter, a type checker, or a test imports it
for something else entirely. As a factory, importing the module does nothing and calling it does
everything.

**What is wired, as of A5 and A6.** The pipeline this assembles listens, persists, classifies,
resolves identity, *continues the conversation*, replies, sends the reply, and files what it did.
A4 deliberately built the orchestrator without a receptionist — one wired before continuity existed
would have forgotten the previous turn on every message, and would have had nothing to send with.
Both of those now exist, so the receptionist, the conversation store and the channel sender are
wired here and the loop is closed: a guest who messages the number gets an answer.

**What G3 added.** An emergency alerter, built from the sender and the operator's own number. It is
the second thing on this list that can be absent, and the more serious of the two: without it the
process still detects an emergency, still answers the guest and still files the item, but the only
alert is a log line.

The degraded states that are allowed are both about the last step, and both are a loud warning at
startup rather than a refusal to start, because everything before that step still works: a process
with no send credentials composes and records replies it cannot put on the wire, and a process with
no operator number cannot tell a person about an emergency. Neither is a state to point a real
guest's number at — see the B4 runbook — and neither is a reason for a service that ingests,
classifies and files to refuse to run.

**What B5 added.** The receptionist, conversation store, sender, alerter and the knowledge base
(2.4's ``configure_knowledge``) above are now wired only when there is no ``REDIS_URL`` — that
whole graph moved to ``orchestration/composition.build_consumer`` so ``apps/api/worker.py`` can
build the identical graph in its own process. See ``assemble``'s docstring for which queue gets
built and why.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from packages.intents.schema import Vocabulary

from apps.api.app import create_app
from apps.api.classifier.factory import build_classifier
from apps.api.classifier.service import Classifier
from apps.api.core.config import Settings, get_settings
from apps.api.db.engine import Database, build_database
from apps.api.db.repository import SessionScopedMessageRepository
from apps.api.db.tenant_resolver import ChannelConfigTenantResolver
from apps.api.db.unclaimed_delivery_repo import SessionScopedUnclaimedDeliveryStore
from apps.api.ingestion.ports import ClassificationQueue
from apps.api.ingestion.preflight import warn_on_unclaimed_endpoints
from apps.api.orchestration.composition import build_consumer
from apps.api.orchestration.queue import (
    RedisClassificationQueue,
    ThreadPoolClassificationQueue,
    build_redis_pool,
)

_logger = logging.getLogger(__name__)


def create_application(settings: Settings | None = None) -> FastAPI:
    """Build the running application from the process environment.

    Raises ``ConfigError`` naming every missing variable at once if the environment is incomplete —
    at startup, before a single message can arrive and be mishandled, which is the whole reason A1
    checks requirements per subsystem rather than at import.
    """
    resolved = settings if settings is not None else get_settings()
    vocabulary = resolved.vocabulary()
    return assemble(
        resolved,
        build_database(resolved),
        build_classifier(resolved, vocabulary=vocabulary),
        vocabulary=vocabulary,
    )


def assemble(
    settings: Settings,
    database: Database,
    classifier: Classifier,
    *,
    vocabulary: Vocabulary | None = None,
) -> FastAPI:
    """Wire the object graph over an already-built database and classifier.

    Separate from :func:`create_application` so the wiring itself can be exercised against SQLite
    and a stub model without a Postgres URL or an API key. This is the function under test; the one
    above is the four lines that decide what to pass it.

    **Which queue, and why this is the only branch in the file (roadmap B5).** ``REDIS_URL`` unset
    is not a degraded state, unlike a missing sender or alerter below — it is the same in-process
    path this service has run on since A4, chosen deliberately for single-instance/dev. Set, this
    process becomes a thin producer: no orchestrator, no sender, no alerter, no per-message DB
    repos — those are the arq worker's job now (`apps/api/worker.py`, wired from the identical
    `orchestration/composition.build_consumer` this branch calls when there is no Redis). Building
    them here anyway would leave an idle sender client open in a process that never uses it.
    """
    # The tenant resolver gets the unstamped scope deliberately (B2): it answers the one question
    # asked *before* a tenant is known — which tenant owns this endpoint — and migration 004 gives
    # that lookup a policy of its own. Every tenant-scoped collaborator below (built inside
    # `build_consumer`, or just `SessionScopedMessageRepository` here) gets `tenant_session`
    # instead, which stamps `app.current_tenant` so RLS enforces on it.
    scope = database.session
    resolve_tenant = ChannelConfigTenantResolver(scope)

    # Receiving and sending read two different sources for the same endpoint (see
    # `ingestion/preflight`). Checked here because this is the only place that holds both, and a
    # warning rather than a check that can fail the boot, like the two below it.
    warn_on_unclaimed_endpoints(resolve_tenant, settings.configured_endpoints())

    redis_dsn = settings.redis_dsn()
    if redis_dsn is not None:
        pool = build_redis_pool(redis_dsn)
        redis_queue = RedisClassificationQueue(pool)
        queue: ClassificationQueue = redis_queue
        sender = None

        async def _close_queue() -> None:
            await redis_queue.aclose()
    else:
        graph = build_consumer(
            settings, database, classifier, vocabulary=vocabulary or settings.vocabulary()
        )
        pool_queue = ThreadPoolClassificationQueue(graph.consumer)
        queue = pool_queue
        sender = graph.sender

        async def _close_queue() -> None:
            pool_queue.shutdown()

    async def _shutdown() -> None:
        await _close_queue()
        if sender is not None:
            sender.close()
        database.dispose()

    app = create_app(
        settings.meta(),
        SessionScopedMessageRepository(database.tenant_session),
        queue,
        resolve_tenant,
        SessionScopedUnclaimedDeliveryStore(scope),
        on_shutdown=_shutdown,
    )

    # Held on the application so nothing here is collected while the process is still serving, and
    # so a test can reach the queue to drain it. Shutdown order is not arbitrary: the queue has to
    # finish (or, for Redis, hand off) the messages it accepted before the engine those messages
    # are writing through closes, and before the sender those messages are replying through closes
    # its connections. That order lives in `_shutdown` above, which `create_app` runs from the
    # application's lifespan.
    app.state.database = database
    app.state.queue = queue
    app.state.sender = sender

    return app
