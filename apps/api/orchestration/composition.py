"""Builds the consumer graph shared by both queue transports (roadmap B5).

Before B5 there was exactly one process that ever consumed a message, so `main.py` wired the
`Orchestrator` and everything it depends on inline. B5 adds a second consumer — the arq worker,
running in its own OS process (`apps/api/worker.py`) — and a change to the sender, the alerter or
the orchestrator's wiring silently absent from one of the two processes is exactly the kind of bug
that only shows up in production. This module is the one construction both processes call, so
there is only one place that wiring can drift from.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.intents.schema import default_vocabulary

from apps.api.channels.factory import build_alerter, build_sender
from apps.api.channels.sender import ChannelSender
from apps.api.classifier.service import Classifier
from apps.api.conversations.receptionist import handle
from apps.api.core.config import Settings
from apps.api.db.engine import Database
from apps.api.db.orchestration_repo import (
    SqlAlchemyAuditLog,
    SqlAlchemyClassificationWriter,
    SqlAlchemyConversationStore,
    SqlAlchemyCrmLookup,
    SqlAlchemyInboxWriter,
    SqlAlchemyMessageLoader,
)
from apps.api.orchestration.queue import MessageConsumer
from apps.api.orchestration.worker import Orchestrator


@dataclass(frozen=True, slots=True)
class ConsumerGraph:
    """The wired consumer, plus the one collaborator its owner has to close on shutdown."""

    consumer: MessageConsumer
    sender: ChannelSender | None


def build_consumer(settings: Settings, database: Database, classifier: Classifier) -> ConsumerGraph:
    """Wire one `MessageConsumer` — the orchestrator, its senders, alerter, and DB repos.

    Tenant-scoped throughout (B2): every repository below takes `database.tenant_session`, so
    migration 004's RLS policies enforce on this consumer exactly as they do on the request path.
    """
    tenant_scope = database.tenant_session
    sender = build_sender(settings)
    alerter = build_alerter(
        sender,
        settings.control_chat_phone_e164,
        declared_channel=default_vocabulary().emergency.alert,
    )
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
        alerter=alerter,
    )
    consumer = MessageConsumer(SqlAlchemyMessageLoader(tenant_scope), orchestrator)
    return ConsumerGraph(consumer=consumer, sender=sender)
