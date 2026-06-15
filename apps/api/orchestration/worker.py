"""The orchestrator: one message through the full pipeline (addendum §5 → §12).

Flow per message: optional media → classify → identity → rules → confidence-band routing → audit +
inbox item. The decision is returned as a :class:`ProcessOutcome`; destination delivery and
control-chat pings are executed from it by their own modules (kept out of here for testability).

Routing (DECISIONS.md / v1.2 §3 rubric):
* a matching **rule** auto-routes (audit ``actor=bot``), regardless of band;
* else **HIGH** → auto-route, **MEDIUM** → control-chat ping, **LOW** / unclear → inbox for review.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apps.api.audit.log import AuditEntry, AuditLog
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import input_from
from apps.api.core.policy import DEFAULT_POLICY, TenantPolicy
from apps.api.identity.resolver import IncomingContact, resolve
from apps.api.media.pipeline import MediaPipeline
from apps.api.orchestration.ports import CrmLookup, InboxItemDraft, InboxWriter, RulesProvider
from apps.api.rules.engine import RuleContext, evaluate
from apps.api.schemas.enums import ConfidenceBand, IdentityDecision, InboxStatus
from apps.api.schemas.message import MessageEnvelope


class RoutingAction(StrEnum):
    AUTO_ROUTE = "auto_route"
    CONTROL_PING = "control_ping"
    INBOX_REVIEW = "inbox_review"


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """What the orchestrator decided for one message (for delivery handlers + metrics)."""

    action: RoutingAction
    band: ConfidenceBand | None
    is_unclear: bool
    matched_rule_id: str | None
    identity_decision: IdentityDecision | None
    destination_id: str | None


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
    ) -> None:
        self._classifier = classifier
        self._audit = audit
        self._inbox = inbox
        self._rules_provider = rules_provider
        self._crm_lookup = crm_lookup
        self._media = media
        self._policy = policy

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
        sender_is_new = resolution.decision is IdentityDecision.NEW

        rule = evaluate(
            self._rules_provider(tenant_id),
            RuleContext(
                sender_phone_e164=message.sender_phone_e164,
                message_text=message.classifiable_text or "",
                sender_is_new=sender_is_new,
            ),
        )
        band = self._policy.band(result.confidence_overall)
        snapshot = result.model_dump(mode="json")

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
        )
