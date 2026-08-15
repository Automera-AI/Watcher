"""The orchestrator: one message through the full pipeline (addendum §5 → §12).

Flow per message: optional media → classify → record the classification → identity → conversation
continuity → receptionist → send the reply → audit + inbox item. The decision is returned as a
:class:`ProcessOutcome`.

**What A5 changed, and why it is the whole item.** Two things used to be missing and they were the
difference between a system that files and a receptionist that holds a conversation:

* *Continuity.* The receptionist was called — when it was called at all — with ``task=None`` and no
  conversation, so every message opened a brand-new job and forgot the previous turn. It now runs
  against a :class:`~apps.api.orchestration.ports.ConversationStore`: the inbound turn is recorded,
  the active task is loaded, the updated task and the reply are written back. ``task_rows`` stops
  being a table nothing writes to.
* *The v1 filer is gone.* Rules and destinations were v1's answer to an inbound message: match a
  rule, route it to a Sheet. A message that gets *answered* has nowhere to be filed to, and keeping
  both answers meant a message could be auto-routed and replied to at the same time. The rule
  engine and both tables are retained for the control page (track D), where a human routes
  something on purpose; what is gone is this module's dependency on them.

**Routing, after the excision.** With a receptionist wired, every classified message reaches it and
the receptionist's own autonomy check decides the shape of the reply: a task it may progress →
``RECEPTIONIST_REPLY``; anything the vocabulary reserves for a person → ``HANDOFF``, which still
answers the guest ("let me connect you with someone") and still files the item for a human. Without
a receptionist the pipeline degrades to filing by band: MEDIUM or better pings the control chat,
LOW and unreadable messages wait in the inbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from apps.api.audit.log import AuditEntry, AuditLog
from apps.api.channels.sender import ChannelSender
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import ClassificationOutcome, input_from
from apps.api.conversations.task import Task
from apps.api.core.autonomy import Autonomy, decide_autonomy
from apps.api.core.policy import DEFAULT_POLICY, TenantPolicy
from apps.api.identity.resolver import IncomingContact, resolve
from apps.api.media.pipeline import MediaPipeline
from apps.api.orchestration.ports import (
    ClassificationDraft,
    ClassificationWriter,
    ConversationStore,
    CrmLookup,
    InboxItemDraft,
    InboxWriter,
)
from apps.api.schemas.enums import ConfidenceBand, IdentityDecision, InboxStatus
from apps.api.schemas.envelope import InboundTurn, OutboundAction, to_inbound_turn
from apps.api.schemas.message import MessageEnvelope

_logger = logging.getLogger(__name__)


class Receptionist(Protocol):
    """Callable that drives a task within a conversation."""

    async def __call__(
        self,
        turn: InboundTurn,
        intent: str,
        confidence: float,
        extracted_slots: dict[str, str],
        task: Task | None,
        *,
        identity_verified: bool,
        emergency: bool,
        turns_taken: int,
    ) -> tuple[OutboundAction, Task]: ...


class RoutingAction(StrEnum):
    CONTROL_PING = "control_ping"
    INBOX_REVIEW = "inbox_review"
    RECEPTIONIST_REPLY = "receptionist_reply"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """What the orchestrator decided for one message (for delivery handlers + metrics)."""

    action: RoutingAction
    band: ConfidenceBand | None
    is_unclear: bool
    identity_decision: IdentityDecision | None
    autonomy: Autonomy | None = None
    outbound_action: OutboundAction | None = None
    classification_id: str | None = None
    delivered: bool | None = None
    """``None`` when there was nothing to send; ``False`` when a composed reply did not go out."""


class Orchestrator:
    """Runs one message through the pipeline and records the decision (audit + inbox)."""

    def __init__(
        self,
        classifier: Classifier,
        audit: AuditLog,
        inbox: InboxWriter,
        crm_lookup: CrmLookup,
        *,
        media: MediaPipeline | None = None,
        policy: TenantPolicy = DEFAULT_POLICY,
        receptionist: Receptionist | None = None,
        conversations: ConversationStore | None = None,
        sender: ChannelSender | None = None,
        classifications: ClassificationWriter | None = None,
        logger: logging.Logger = _logger,
    ) -> None:
        if (receptionist is None) != (conversations is None):
            raise ValueError(
                "receptionist and conversations must be configured together: a receptionist "
                "without a conversation store forgets the previous turn on every message, which "
                "is the exact failure A5 exists to remove"
            )
        self._classifier = classifier
        self._audit = audit
        self._inbox = inbox
        self._crm_lookup = crm_lookup
        self._media = media
        self._policy = policy
        self._receptionist = receptionist
        self._conversations = conversations
        self._sender = sender
        self._classifications = classifications
        self._logger = logger

    async def process(
        self,
        tenant_id: str,
        message_id: str,
        message: MessageEnvelope,
        history: list[MessageEnvelope] | None = None,
    ) -> ProcessOutcome:
        """Handle one message end to end.

        Asynchronous because the receptionist and the channel sender are, and because the previous
        arrangement — a synchronous ``process`` calling ``asyncio.run`` around the receptionist —
        opens and tears down an event loop per message and cannot be called from anywhere that
        already has one. The caller owns the loop now (see ``orchestration/queue.py``).

        The database work inside is still synchronous. Each message runs on its own worker thread
        with its own loop, so a blocking query blocks nothing but the message it belongs to.
        """
        if self._media is not None and message.media_id is not None:
            message = self._media.enrich(tenant_id, message)

        outcome = self._classifier.classify(input_from(message, history or []))

        if outcome.result is None:
            return self._finish(
                tenant_id,
                message_id,
                action=RoutingAction.INBOX_REVIEW,
                status=InboxStatus.NEEDS_REVIEW,
                band=ConfidenceBand.LOW,
                audit_action="unclassified",
                snapshot={},
                is_unclear=True,
                identity_decision=None,
            )

        result = outcome.result
        classification_id = self._record_classification(tenant_id, message_id, outcome)

        incoming = IncomingContact(
            phone_e164=message.sender_phone_e164,
            name=result.person_name,
            company=result.company_name,
        )
        resolution = resolve(
            incoming,
            self._crm_lookup(tenant_id, incoming),
            merge_threshold=self._policy.identity_merge_threshold,
            review_threshold=self._policy.identity_review_threshold,
        )
        identity_verified = resolution.decision is IdentityDecision.MERGE

        band = self._policy.band(result.confidence_overall)
        snapshot = result.model_dump(mode="json")

        if self._receptionist is not None and self._conversations is not None:
            return await self._converse(
                tenant_id,
                message_id,
                message,
                result_intent=result.intent,
                confidence=result.confidence_overall,
                identity_verified=identity_verified,
                band=band,
                snapshot=snapshot,
                identity_decision=resolution.decision,
                classification_id=classification_id,
            )

        # No receptionist wired: file by band. Nothing is auto-*routed* any more — there is no
        # destination to route to — so the only question left is how loudly a person is told.
        if band is ConfidenceBand.LOW:
            action, status, audit_action = (
                RoutingAction.INBOX_REVIEW,
                InboxStatus.NEEDS_REVIEW,
                "needs_review",
            )
        else:
            action, status, audit_action = (
                RoutingAction.CONTROL_PING,
                InboxStatus.PENDING,
                "control_ping",
            )

        return self._finish(
            tenant_id,
            message_id,
            action=action,
            status=status,
            band=band,
            audit_action=audit_action,
            snapshot=snapshot,
            is_unclear=False,
            identity_decision=resolution.decision,
            classification_id=classification_id,
        )

    async def _converse(
        self,
        tenant_id: str,
        message_id: str,
        message: MessageEnvelope,
        *,
        result_intent: str,
        confidence: float,
        identity_verified: bool,
        band: ConfidenceBand,
        snapshot: dict[str, object],
        identity_decision: IdentityDecision | None,
        classification_id: str | None,
    ) -> ProcessOutcome:
        """The receptionist path: continue the conversation, reply, and record both halves."""
        assert self._receptionist is not None and self._conversations is not None

        turn = to_inbound_turn(UUID(tenant_id), message)
        state = self._conversations.begin(turn)

        # `emergency` is still hardcoded. Detecting one is roadmap G3, which owns the vocabulary's
        # emergency triggers and the alert path; asserting `False` here is the honest statement
        # that nothing checks yet — and answering made it worse, not better. A gas leak used to be
        # filed in silence; it now gets a confident, polite reply about maintenance. G3 is not
        # optional before a real guest can reach this number.
        action, task = await self._receptionist(
            turn,
            result_intent,
            confidence,
            {},  # slot extraction is a prompt change (the model emits no slots today); item 2.x
            state.task,
            identity_verified=identity_verified,
            emergency=False,
            turns_taken=state.replies_sent,
        )

        # Recorded before it is sent, deliberately. A reply we sent but did not record makes us
        # ask the same question again on the next turn; a reply we recorded but failed to send
        # leaves a visible, correctable gap in one conversation.
        self._conversations.record_reply(state, turn, task, action)
        delivered = await self._deliver(action, turn)

        handed_off = action.kind == "handoff"
        autonomy: Autonomy = (
            "hand_off"
            if handed_off
            else decide_autonomy(result_intent, confidence, identity_verified=identity_verified)
        )

        return self._finish(
            tenant_id,
            message_id,
            action=RoutingAction.HANDOFF if handed_off else RoutingAction.RECEPTIONIST_REPLY,
            # A handed-off conversation is a person's job now, so it is filed as such even though
            # the guest has already been answered.
            status=InboxStatus.NEEDS_REVIEW if handed_off else InboxStatus.AUTO_ROUTED,
            band=band,
            audit_action="handed_off" if handed_off else "receptionist_reply",
            snapshot=snapshot,
            is_unclear=False,
            identity_decision=identity_decision,
            classification_id=classification_id,
            autonomy=autonomy,
            outbound_action=action,
            delivered=delivered,
        )

    async def _deliver(self, action: OutboundAction, turn: InboundTurn) -> bool | None:
        """Put the reply on the wire. ``None`` when no sender is configured at all.

        A send that fails is logged and swallowed: the message has been classified, the reply has
        been recorded, and raising here would lose all of that to a transient 502 from the channel.
        The failure is visible in ``delivered`` and in the log, which is what a retry (B5) will
        eventually read.
        """
        if self._sender is None:
            return None
        try:
            await self._sender.send(action, turn)
        except Exception:
            self._logger.exception(
                "reply not delivered: conversation=%s kind=%s",
                turn.channel_thread_id,
                action.kind,
            )
            return False
        return True

    def _record_classification(
        self, tenant_id: str, message_id: str, outcome: ClassificationOutcome
    ) -> str | None:
        """Write the ``classifications`` row, if a writer is configured.

        Only ever called with a classified outcome — the unclear path returns before this, because
        a row on that table describes a result and there is no result to describe.
        """
        if self._classifications is None or outcome.result is None:
            return None
        return self._classifications.record(
            ClassificationDraft(
                tenant_id=tenant_id,
                message_id=message_id,
                result=outcome.result,
                model_used=outcome.model_used,
                prompt_version=outcome.prompt_version,
                latency_ms=outcome.latency_ms,
            )
        )

    def _finish(
        self,
        tenant_id: str,
        message_id: str,
        *,
        action: RoutingAction,
        status: InboxStatus,
        band: ConfidenceBand,
        audit_action: str,
        snapshot: dict[str, object],
        is_unclear: bool,
        identity_decision: IdentityDecision | None,
        classification_id: str | None = None,
        autonomy: Autonomy | None = None,
        outbound_action: OutboundAction | None = None,
        delivered: bool | None = None,
    ) -> ProcessOutcome:
        self._audit.write(
            AuditEntry(
                tenant_id=tenant_id,
                message_id=message_id,
                action=audit_action,
                actor="bot",
                classification_snapshot=snapshot,
            )
        )
        self._inbox.create(
            InboxItemDraft(
                tenant_id=tenant_id,
                message_id=message_id,
                status=status,
                band=band,
                classification_id=classification_id,
                snapshot=snapshot,
            )
        )
        return ProcessOutcome(
            action=action,
            band=band,
            is_unclear=is_unclear,
            identity_decision=identity_decision,
            autonomy=autonomy,
            outbound_action=outbound_action,
            classification_id=classification_id,
            delivered=delivered,
        )
