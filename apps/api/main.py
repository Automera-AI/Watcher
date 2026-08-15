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

The one degraded state that is still allowed is a process with no send credentials. It ingests,
classifies, continues conversations and records the replies it composed — it simply cannot put them
on the wire, which is every deploy between B1 and B4. That is a loud warning at startup rather than
a refusal to start, because everything except the last step still works.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from apps.api.app import create_app
from apps.api.channels.factory import build_sender
from apps.api.classifier.factory import build_classifier
from apps.api.classifier.service import Classifier
from apps.api.conversations.receptionist import handle
from apps.api.core.config import Settings, get_settings
from apps.api.db.engine import Database, build_database
from apps.api.db.orchestration_repo import (
    SqlAlchemyAuditLog,
    SqlAlchemyClassificationWriter,
    SqlAlchemyConversationStore,
    SqlAlchemyCrmLookup,
    SqlAlchemyInboxWriter,
    SqlAlchemyMessageLoader,
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
    # Two scopes, and which one a collaborator gets is a security decision (B2). `tenant_scope`
    # stamps `app.current_tenant` on the transaction, so the RLS policies in migration 004 are
    # enforcing on every one of these. The resolver gets the unstamped scope because it is the one
    # question asked *before* a tenant is known — which tenant owns this endpoint — and migration
    # 004 gives that lookup a policy of its own.
    tenant_scope = database.tenant_session
    scope = database.session
    sender = build_sender(settings)

    orchestrator = Orchestrator(
        classifier,
        SqlAlchemyAuditLog(tenant_scope),
        SqlAlchemyInboxWriter(tenant_scope),
        SqlAlchemyCrmLookup(tenant_scope),
        policy=settings.tenant_policy(),
        receptionist=handle,
        conversations=SqlAlchemyConversationStore(tenant_scope),
        sender=sender,
        classifications=SqlAlchemyClassificationWriter(tenant_scope),
    )
    queue = ThreadPoolClassificationQueue(
        MessageConsumer(SqlAlchemyMessageLoader(tenant_scope), orchestrator)
    )

    def _shutdown() -> None:
        queue.shutdown()
        if sender is not None:
            sender.close()
        database.dispose()

    app = create_app(
        settings.meta(),
        SessionScopedMessageRepository(tenant_scope),
        queue,
        ChannelConfigTenantResolver(scope),
        on_shutdown=_shutdown,
    )

    # Held on the application so nothing here is collected while the process is still serving, and
    # so a test can reach the queue to drain it. Shutdown order is not arbitrary: the pool has to
    # finish the messages it accepted before the engine those messages are writing through closes,
    # and before the sender those messages are replying through closes its connections. That order
    # lives in `_shutdown` above, which `create_app` runs from the application's lifespan.
    app.state.database = database
    app.state.queue = queue
    app.state.sender = sender

    return app
