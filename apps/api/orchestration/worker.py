"""The orchestrator: one message through the full pipeline (addendum §5 → §12).

Flow per message: optional media → classify → identity → autonomy gate → rules →
confidence-band routing → audit + inbox item. The decision is returned as a
:class:`ProcessOutcome`; destination delivery and control-chat pings are executed from it by
their own modules (kept out of here for testability).

Routing (DECISIONS.md / v1.2 §3 rubric):
* autonomy gate says **act** / **act_and_notify** → receptionist handles → ``RECEPTIONIST_REPLY``
* autonomy gate says **hand_off** → fall through to existing routing:
  * a matching **rule** auto-routes (audit ``actor=bot``), regardless of band;
  * else **HIGH** → auto-route, **MEDIUM** → control-chat ping, **LOW** / unclear → inbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from apps.api.audit.log import AuditEntry, AuditLog
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import input_from
from apps.api.conversations.task import Task
from apps.api.core.autonomy import Autonomy, decide_autonomy
from apps.api.core.policy import DEFAULT_POLICY, TenantPolicy
from apps.api.identity.resolver import IncomingContact, resolve
from apps.api.media.pipeline import MediaPipeline
from apps.api.orchestration.ports import CrmLookup, InboxItemDraft, InboxWriter, RulesProvider
from apps.api.rules.engine import RuleContext, evaluate
from apps.api.schemas.enums import ConfidenceBand, IdentityDecision, InboxStatus
from apps.api.schemas.envelope import InboundTurn, OutboundAction, to_inbound_turn
from apps.api.schemas.message import MessageEnvelope


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
    ) -> tuple[OutboundAction, Task]: ...


class RoutingAction(StrEnum):
    AUTO_ROUTE = "auto_route"
    CONTROL_PING = "control_ping"
    INBOX_REVIEW = "inbox_review"
    RECEPTIONIST_REPLY = "receptionist_reply"


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """What the orchestrator decided for one message (for delivery handlers + metrics)."""

    action: RoutingAction
    band: ConfidenceBand | None
    is_unclear: bool
    matched_rule_id: str | None
    identity_decision: IdentityDecision | None
    destination_id: str | None
    autonomy: Autonomy | None = None
    outbound_action: OutboundAction | None = None


class Orchestrator:
    """Runs one message through the pipeline and records the decision (audit + inbox)."""

    def __init__(
        self,
        classifier: Classifier,
        audit: AuditLog,
        inbox: InboxWriter,
        rules_provider: RulesProvider,
        crm_lookup: CrmLookup,
        *,
        media: MediaPipeline | None = None,
        policy: TenantPolicy = DEFAULT_POLICY,
        receptionist: Receptionist | None = None,
    ) -> None:
        self._classifier = classifier
        self._audit = audit
        self._inbox = inbox
        self._rules_provider = rules_provider
        self._crm_lookup = crm_lookup
        self._media = media
        self._policy = policy
        self._receptionist = receptionist

    def process(
        self,
        tenant_id: str,
        message_id: str,
        message: MessageEnvelope,
        history: list[MessageEnvelope] | None = None,
    ) -> ProcessOutcome:
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
                model_used=outcome.model_used,
                snapshot={},
                is_unclear=True,
                identity_decision=None,
                matched_rule_id=None,
                destination_id=None,
            )

        result = outcome.result
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
        sender_is_new = resolution.decision is IdentityDecision.NEW

        band = self._policy.band(result.confidence_overall)
        snapshot = result.model_dump(mode="json")

        if self._receptionist is not None:
            autonomy = decide_autonomy(
                result.intent,
                result.confidence_overall,
                identity_verified=identity_verified,
            )
            if autonomy != "hand_off":
                import asyncio

                turn = to_inbound_turn(UUID(tenant_id), message)
                outbound, _task = asyncio.run(
                    self._receptionist(
                        turn,
                        result.intent,
                        result.confidence_overall,
                        {},
                        None,
                        identity_verified=identity_verified,
                        emergency=False,
                    )
                )
                return self._finish(
                    tenant_id,
                    message_id,
                    action=RoutingAction.RECEPTIONIST_REPLY,
                    status=InboxStatus.AUTO_ROUTED,
                    band=band,
                    audit_action="receptionist_reply",
                    model_used=outcome.model_used,
                    snapshot=snapshot,
                    is_unclear=False,
                    identity_decision=resolution.decision,
                    matched_rule_id=None,
                    destination_id=None,
                    autonomy=autonomy,
                    outbound_action=outbound,
                )

        rule = evaluate(
            self._rules_provider(tenant_id),
            RuleContext(
                sender_phone_e164=message.sender_phone_e164,
                message_text=message.classifiable_text or "",
                sender_is_new=sender_is_new,
            ),
        )

        if rule is not None:
            return self._finish(
                tenant_id,
                message_id,
                action=RoutingAction.AUTO_ROUTE,
                status=InboxStatus.AUTO_ROUTED,
                band=band,
                audit_action="auto_routed",
                model_used=outcome.model_used,
                snapshot=snapshot,
                is_unclear=False,
                identity_decision=resolution.decision,
                matched_rule_id=rule.id,
                destination_id=rule.action.destination_id,
            )

        if band is ConfidenceBand.HIGH:
            action, status, audit_action = (
                RoutingAction.AUTO_ROUTE,
                InboxStatus.AUTO_ROUTED,
                "auto_routed",
            )
        elif band is ConfidenceBand.MEDIUM:
            action, status, audit_action = (
                RoutingAction.CONTROL_PING,
                InboxStatus.PENDING,
                "control_ping",
            )
        else:
            action, status, audit_action = (
                RoutingAction.INBOX_REVIEW,
                InboxStatus.NEEDS_REVIEW,
                "needs_review",
            )

        return self._finish(
            tenant_id,
            message_id,
            action=action,
            status=status,
            band=band,
            audit_action=audit_action,
            model_used=outcome.model_used,
            snapshot=snapshot,
            is_unclear=False,
            identity_decision=resolution.decision,
            matched_rule_id=None,
            destination_id=None,
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
        model_used: str | None,
        snapshot: dict[str, object],
        is_unclear: bool,
        identity_decision: IdentityDecision | None,
        matched_rule_id: str | None,
        destination_id: str | None,
        autonomy: Autonomy | None = None,
        outbound_action: OutboundAction | None = None,
    ) -> ProcessOutcome:
        self._audit.write(
            AuditEntry(
                tenant_id=tenant_id,
                message_id=message_id,
                action=audit_action,
                actor="bot",
                classification_snapshot=snapshot,
                destination_id=destination_id,
            )
        )
        self._inbox.create(
            InboxItemDraft(
                tenant_id=tenant_id,
                message_id=message_id,
                status=status,
                band=band,
                model_used=model_used,
                assigned_destination_id=destination_id,
                snapshot=snapshot,
            )
        )
        return ProcessOutcome(
            action=action,
            band=band,
            is_unclear=is_unclear,
            matched_rule_id=matched_rule_id,
            identity_decision=identity_decision,
            destination_id=destination_id,
            autonomy=autonomy,
            outbound_action=outbound_action,
        )
