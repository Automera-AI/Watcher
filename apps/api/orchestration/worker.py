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

**What G3 changed, and where it sits.** One check now runs before all of the above: a message is
tested against the vocabulary's emergency triggers *before* it is classified, identified or
answered. That ordering is the item. ``intents.yaml`` has said "checked before intent, before
confidence, before anything" since 0.3, and it is not a stylistic preference — classification is a
network call to a model that can be slow, can be wrong, and can fail, and none of those may stand
between a guest saying *smell of gas* and an operator's phone. An emergency therefore skips the
classifier entirely: it is not filed as an intent, because the vocabulary's instruction is to stop
being useful at the safety line and fetch a person.

**Routing, after the excision.** With a receptionist wired, every classified message reaches it and
the receptionist's own autonomy check decides the shape of the reply: a task it may progress →
``RECEPTIONIST_REPLY``; anything the vocabulary reserves for a person → ``HANDOFF``, which still
answers the guest ("let me connect you with someone") and still files the item for a human. Without
a receptionist the pipeline degrades to filing by band: MEDIUM or better pings the control chat,
LOW and unreadable messages wait in the inbox.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.audit.log import AuditEntry, AuditLog
from apps.api.channels.sender import ChannelSender
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import ClassificationOutcome, input_from
from apps.api.conversations.slots import normalise_slots
from apps.api.conversations.task import Task
from apps.api.core.alerts import LOG_ONLY, AlertOutcome, EmergencyAlert, OperatorAlerter
from apps.api.core.autonomy import Autonomy, decide_autonomy
from apps.api.core.emergency import EmergencyDetection, detect, emergency_reply
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
        vocabulary: Vocabulary | None,
        conversation_id: str | None,
        today: date,
    ) -> tuple[OutboundAction, Task]: ...


class RoutingAction(StrEnum):
    CONTROL_PING = "control_ping"
    INBOX_REVIEW = "inbox_review"
    RECEPTIONIST_REPLY = "receptionist_reply"
    HANDOFF = "handoff"
    #: A trigger fired. Distinct from HANDOFF, which is also a person's job: an emergency was
    #: never classified, so nothing about it can be read as a decision the model made (G3).
    EMERGENCY = "emergency"


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

    emergency: EmergencyDetection | None = None
    """The trigger that fired, when ``action`` is ``EMERGENCY``. ``None`` on every other path."""

    alerted: bool | None = None
    """Whether a human was reached. ``None`` when no alert was attempted (no emergency, or no
    alerter configured — the two are told apart by ``emergency``, which is set either way)."""


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
        alerter: OperatorAlerter | None = None,
        vocabulary: Vocabulary | None = None,
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
        self._alerter = alerter
        # The tenant's own vocabulary (``TENANT_VERTICAL``). Held rather than fetched per call so
        # the autonomy ceiling, the slots absorbed and the receptionist's task machine are all
        # reading the same file — a clinic served out of the holiday-home vocabulary would collect
        # ``check_in`` for an appointment and look up ceilings for intents it never emits.
        self._vocabulary = vocabulary or default_vocabulary()
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

        # Before the classifier, deliberately, and after the media pipeline for the same reason:
        # a voice note saying there is a fire only has text once it has been transcribed. See the
        # module docstring — this ordering is what roadmap G3 is.
        emergency = detect(
            message.classifiable_text,
            at=message.received_at,
            timezone=self._policy.timezone,
            vocabulary=self._vocabulary,
        )
        if emergency is not None:
            return await self._emergency(tenant_id, message_id, message, emergency)

        outcome = self._classifier.classify(
            input_from(message, history or [], timezone=self._policy.timezone)
        )

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
                extracted_slots=result.extracted_slots,
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

    async def _emergency(
        self,
        tenant_id: str,
        message_id: str,
        message: MessageEnvelope,
        detection: EmergencyDetection,
    ) -> ProcessOutcome:
        """A trigger fired: answer the guest, reach a person, file it, and stop (roadmap G3).

        Four things happen and the *order* is the safety property:

        1. the inbound turn and the reply are recorded, before either goes anywhere, so a crash
           mid-emergency leaves a transcript a person can read rather than nothing;
        2. the reply and the operator alert are dispatched **concurrently**. Sequentially, one of
           them waits on the other's retries — and neither "the guest hears nothing for ten
           seconds while we retry the alert" nor "the operator waits while we retry the guest" is
           an acceptable way to lose that time;
        3. the item is filed ``NEEDS_REVIEW`` at the top band, because a person is now doing this;
        4. nothing else runs. No classification, no identity resolution, no receptionist, no task.
           The vocabulary's instruction is to stop being useful beyond the safety line.

        The task already in flight, if any, is left alone rather than abandoned. A guest with a
        gas leak may well come back to their booking question afterwards, and the conversation is
        a person's now either way — throwing away what the receptionist had collected would only
        make the resumed conversation worse.
        """
        turn = to_inbound_turn(UUID(tenant_id), message)
        action = OutboundAction(
            kind="handoff",
            text=emergency_reply(self._policy.urgent_contact, self._policy.emergency_reply),
        )

        if self._conversations is not None:
            state = self._conversations.begin(turn)
            # No task: an emergency is not a job the receptionist is working on, and inventing
            # one would put a fake intent on `task_rows` for the control page to explain.
            self._conversations.record_reply(state, turn, None, action)

        delivered, alerted = await asyncio.gather(
            self._deliver(action, turn),
            self._raise_the_alarm(tenant_id, message_id, message, turn, detection),
        )

        return self._finish(
            tenant_id,
            message_id,
            action=RoutingAction.EMERGENCY,
            status=InboxStatus.NEEDS_REVIEW,
            # Not a model's confidence — a phrase an operator wrote down was present. The band
            # exists so the control page can sort, and nothing sorts above this.
            band=ConfidenceBand.HIGH,
            audit_action="emergency",
            snapshot=detection.snapshot(),
            is_unclear=False,
            identity_decision=None,
            autonomy="hand_off",
            outbound_action=action,
            delivered=delivered,
            emergency=detection,
            alerted=alerted,
        )

    async def _raise_the_alarm(
        self,
        tenant_id: str,
        message_id: str,
        message: MessageEnvelope,
        turn: InboundTurn,
        detection: EmergencyDetection,
    ) -> bool | None:
        """Reach a human. Returns whether one was reached; ``None`` if nothing could try.

        The CRITICAL log line is written first and unconditionally. It is the one record that does
        not depend on a credential, a network or a configuration being right, and a process whose
        alerter is unset still leaves a trail that says exactly what it saw and did not deliver.
        """
        self._logger.critical(
            "EMERGENCY DETECTED: trigger=%s tenant=%s message=%s",
            detection.trigger_id,
            tenant_id,
            message_id,
        )
        if self._alerter is None:
            self._logger.critical(
                "no alerter configured — the %s alert for message %s was logged and nothing else. "
                "Nobody has been told.",
                detection.alert,
                message_id,
            )
            return None

        outcome: AlertOutcome = await self._alerter.alert(
            EmergencyAlert(
                tenant_id=tenant_id,
                message_id=message_id,
                trigger_id=detection.trigger_id,
                matched=detection.matched,
                guest_identity=turn.channel_identity,
                thread_id=turn.channel_thread_id,
                text=message.classifiable_text,
                received_at=turn.received_at,
                requested_channel=detection.alert,
            )
        )
        if not outcome.satisfies(detection.alert):
            # Expected today: the vocabulary asks for a phone call and this process sends a
            # message. Logged per emergency rather than once at startup because "the operator was
            # messaged, not called" is a fact about this incident that its record should carry.
            self._logger.warning(
                "emergency alert for %s delivered on %r, not the declared %r (delivered=%s)",
                message_id,
                outcome.channel if outcome.delivered else LOG_ONLY,
                detection.alert,
                outcome.delivered,
            )
        return outcome.delivered

    async def _converse(
        self,
        tenant_id: str,
        message_id: str,
        message: MessageEnvelope,
        *,
        result_intent: str,
        confidence: float,
        extracted_slots: dict[str, str],
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

        # `emergency=False` is now a *fact* rather than the placeholder G3 removed: `process`
        # short-circuits above on any message that matched a trigger, so nothing that reaches the
        # receptionist is an emergency. The parameter stays on the seam — `decide_autonomy` takes
        # it too — because the check belonging to one caller is not a reason for the ceiling to
        # stop knowing about it, and a second entry point into the receptionist would need it.
        # Demo step 5. What the model read out of this message, filtered to the slots the chosen
        # intent is allowed to collect and resolved into the forms a booking can act on. ``today``
        # is the tenant's, not the server's: a message at 00:30 in Cairo is still "tomorrow" from
        # the previous day to a server in UTC, and the whole point of the dates here is which day
        # the patient meant.
        extracted = normalise_slots(
            result_intent,
            extracted_slots,
            vocabulary=self._vocabulary,
            today=self._today(message),
        )
        action, task = await self._receptionist(
            turn,
            result_intent,
            confidence,
            extracted,
            state.task,
            identity_verified=identity_verified,
            emergency=False,
            turns_taken=state.replies_sent,
            vocabulary=self._vocabulary,
            conversation_id=state.conversation_id,
            # The same tenant-clock date the slots were resolved against. The receptionist needs
            # it for the one case where it resolves a slot itself: a reply that answers the
            # question it just asked, which the classifier could only label `unclear`.
            today=self._today(message),
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
            else decide_autonomy(
                result_intent,
                confidence,
                identity_verified=identity_verified,
                vocabulary=self._vocabulary,
            )
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

    def _today(self, message: MessageEnvelope) -> date:
        """The date this message arrived on, on the tenant's clock.

        Same conversion ``input_from`` makes for the model, made again here rather than carried
        across: the model is told the date so it can resolve "بكرة", and this is what *checks* the
        answer. Reading the date back out of the prompt we sent would make the check agree with
        the thing it is checking.
        """
        received = message.received_at
        stamped = received if received.tzinfo is not None else received.replace(tzinfo=UTC)
        try:
            return stamped.astimezone(ZoneInfo(self._policy.timezone)).date()
        except (ZoneInfoNotFoundError, ValueError):
            # A zone name `Settings` would have refused at startup. Fall back to the timestamp's
            # own day rather than dropping every date in the conversation.
            return stamped.date()

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
        emergency: EmergencyDetection | None = None,
        alerted: bool | None = None,
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
            emergency=emergency,
            alerted=alerted,
        )
