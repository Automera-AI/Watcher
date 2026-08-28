"""Builds the consumer graph shared by both queue transports (roadmap B5).

Before B5 there was exactly one process that ever consumed a message, so `main.py` wired the
`Orchestrator` and everything it depends on inline. B5 adds a second consumer — the arq worker,
running in its own OS process (`apps/api/worker.py`) — and a change to the sender, the alerter or
the orchestrator's wiring silently absent from one of the two processes is exactly the kind of bug
that only shows up in production. This module is the one construction both processes call, so
there is only one place that wiring can drift from.

**The knowledge base (2.4) lives here too, for the same reason.** `configure_knowledge` populates
`conversations/tools.REGISTRY`, a process-global service locator (D37) — a worker process that
never called it would answer every `answer_from_knowledge` question with the same "I don't know" a
genuine miss gets, silently, because the tool would simply not be registered in that process.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.intents.schema import Vocabulary

from apps.api.channels.factory import build_alerter, build_sender
from apps.api.channels.sender import ChannelSender
from apps.api.classifier.service import Classifier
from apps.api.conversations.receptionist import handle
from apps.api.conversations.tools import (
    configure_clinic,
    configure_conversation_copy,
    configure_knowledge,
)
from apps.api.core.config import Settings
from apps.api.db.clinic_repo import SqlAlchemyClinicRepository
from apps.api.db.engine import Database
from apps.api.db.knowledge_repo import SqlAlchemyFactRepository
from apps.api.db.orchestration_repo import (
    SqlAlchemyAuditLog,
    SqlAlchemyClassificationWriter,
    SqlAlchemyConversationStore,
    SqlAlchemyCrmLookup,
    SqlAlchemyInboxWriter,
    SqlAlchemyMessageLoader,
)
from apps.api.db.property_repo import SqlAlchemyPropertyRepository
from apps.api.orchestration.queue import MessageConsumer
from apps.api.orchestration.worker import Orchestrator

#: The tools a vertical must declare before its clinic catalogue is wired up. All four, not any:
#: a vocabulary that books but cannot check availability is not a vertical this supports.
_CLINIC_TOOLS = frozenset({"check_availability", "quote_price", "hold_slot", "confirm_booking"})


@dataclass(frozen=True, slots=True)
class ConsumerGraph:
    """The wired consumer, plus the one collaborator its owner has to close on shutdown."""

    consumer: MessageConsumer
    sender: ChannelSender | None


def build_consumer(
    settings: Settings,
    database: Database,
    classifier: Classifier,
    *,
    vocabulary: Vocabulary | None = None,
) -> ConsumerGraph:
    """Wire one `MessageConsumer` — the orchestrator, its senders, alerter, and DB repos.

    Tenant-scoped throughout (B2): every repository below takes `database.tenant_session`, so
    migration 004's RLS policies enforce on this consumer exactly as they do on the request path.
    """
    # One vocabulary for the whole graph (``TENANT_VERTICAL``, demo step 5). Read once here rather
    # than fetched by each collaborator: the alert channel, the autonomy ceilings, the slots the
    # receptionist collects and the intents described to the model are four readings of one file,
    # and a process where they disagree is a process that greets a patient in one vertical and
    # books them in another. A caller that has already read it — `main.py`, the arq worker — passes
    # it in rather than paying for the read a second time.
    selected: Vocabulary = vocabulary or settings.vocabulary()
    tenant_scope = database.tenant_session
    sender = build_sender(settings)
    alerter = build_alerter(
        sender,
        settings.control_chat_phone_e164,
        declared_channel=selected.emergency.alert,
    )
    # The knowledge base (roadmap 2.4), now scoped per property (roadmap 2.8). A process-global
    # registry entry rather than a collaborator threaded through the Orchestrator/Receptionist call
    # chain — see `conversations/tools.py`; `configure_knowledge` is the named seam that swaps it
    # in, the same way `register` already populates the registry at import time. The property
    # resolver rides the same tenant-scoped session, so RLS (004) covers the property lookup too.
    configure_knowledge(
        SqlAlchemyFactRepository(tenant_scope),
        SqlAlchemyPropertyRepository(tenant_scope),
    )
    # The receptionist's own words, by the same named-seam pattern. Wired here rather than left
    # to import-time defaults so a deploy that sets nothing still greets and closes correctly,
    # in neutral English that names no client.
    copy = settings.conversation_copy()
    configure_conversation_copy(copy)
    # The booking journey (demo step 6), for the verticals that have one. Registered only when the
    # tenant's vocabulary actually declares these tools: registering them for a holiday-home deploy
    # would put four names in the registry with an empty clinic catalogue behind them, and
    # `validate_registry` exists to say that a tool nobody declared should not be there. Where they
    # are absent nothing changes — an intent naming an unregistered tool hands off.
    if _CLINIC_TOOLS <= {intent.terminal_tool for intent in selected.intents}:
        configure_clinic(
            SqlAlchemyClinicRepository(tenant_scope),
            timezone=settings.tenant_timezone,
            reference_prefix=settings.tenant_booking_reference_prefix,
            copy=copy,
        )
    orchestrator = Orchestrator(
        classifier,
        SqlAlchemyAuditLog(tenant_scope),
        SqlAlchemyInboxWriter(tenant_scope),
        SqlAlchemyCrmLookup(tenant_scope),
        policy=settings.tenant_policy(),
        receptionist=handle,
        conversations=SqlAlchemyConversationStore(tenant_scope, vocabulary=selected),
        sender=sender,
        classifications=SqlAlchemyClassificationWriter(tenant_scope),
        alerter=alerter,
        vocabulary=selected,
    )
    consumer = MessageConsumer(SqlAlchemyMessageLoader(tenant_scope), orchestrator)
    return ConsumerGraph(consumer=consumer, sender=sender)
