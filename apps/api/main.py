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

**What is wired, and what is deliberately not.** The pipeline this assembles listens, persists,
classifies, resolves identity, applies rules and files the outcome. It does not yet reply: the
orchestrator is built without a receptionist, because wiring one before conversation continuity
exists produces a receptionist with no memory of the previous turn (roadmap A5), and it has nothing
to send a reply with until the outbound sender exists (A6). Filing works end to end; answering is
the next two items, in that order.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from apps.api.app import create_app
from apps.api.classifier.factory import build_classifier
from apps.api.classifier.service import Classifier
from apps.api.core.config import Settings, get_settings
from apps.api.db.engine import Database, build_database
from apps.api.db.orchestration_repo import (
    SqlAlchemyAuditLog,
    SqlAlchemyCrmLookup,
    SqlAlchemyInboxWriter,
    SqlAlchemyMessageLoader,
    SqlAlchemyRulesProvider,
)
from apps.api.db.repository import SessionScopedMessageRepository
from apps.api.db.tenant_resolver import ChannelConfigTenantResolver
from apps.api.orchestration.queue import MessageConsumer, ThreadPoolClassificationQueue
from apps.api.orchestration.worker import Orchestrator

_logger = logging.getLogger(__name__)


def create_application(settings: Settings | None = None) -> FastAPI:
    """Build the running application from the process environment.

    Raises ``ConfigError`` naming every missing variable at once if the environment is incomplete —
    at startup, before a single message can arrive and be mishandled, which is the whole reason A1
    checks requirements per subsystem rather than at import.
    """
    resolved = settings if settings is not None else get_settings()
    return assemble(resolved, build_database(resolved), build_classifier(resolved))


def assemble(settings: Settings, database: Database, classifier: Classifier) -> FastAPI:
    """Wire the object graph over an already-built database and classifier.

    Separate from :func:`create_application` so the wiring itself can be exercised against SQLite
    and a stub model without a Postgres URL or an API key. This is the function under test; the one
    above is the four lines that decide what to pass it.
    """
    scope = database.session

    orchestrator = Orchestrator(
        classifier,
        SqlAlchemyAuditLog(scope),
        SqlAlchemyInboxWriter(scope),
        SqlAlchemyRulesProvider(scope),
        SqlAlchemyCrmLookup(scope),
        policy=settings.tenant_policy(),
    )
    queue = ThreadPoolClassificationQueue(
        MessageConsumer(SqlAlchemyMessageLoader(scope), orchestrator)
    )

    app = create_app(
        settings.meta(),
        SessionScopedMessageRepository(scope),
        queue,
        ChannelConfigTenantResolver(scope),
    )

    # Held on the application so nothing here is collected while the process is still serving, and
    # so a test can reach the queue to drain it. Shutdown order is not arbitrary: the pool has to
    # finish the messages it accepted before the engine those messages are writing through closes.
    app.state.database = database
    app.state.queue = queue

    def _shutdown() -> None:
        queue.shutdown()
        database.dispose()

    app.add_event_handler("shutdown", _shutdown)
    return app
