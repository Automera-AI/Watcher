"""Tests for the orchestrator decision tree (addendum §5 → §12)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apps.api.audit.log import AuditEntry
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import ClassificationOutcome
from apps.api.conversations.receptionist import handle as receptionist_handle
from apps.api.identity.models import CrmRecord
from apps.api.identity.resolver import IncomingContact
from apps.api.orchestration.ports import InboxItemDraft
from apps.api.orchestration.worker import Orchestrator, RoutingAction
from apps.api.rules.models import Rule, RuleAction, SenderIsNew
from apps.api.schemas.enums import (
    ConfidenceBand,
    IdentityDecision,
    InboxStatus,
    MessageType,
    SourceKind,
)
from apps.api.schemas.message import MessageEnvelope

TENANT = "tenant-1"
TENANT_UUID = "00000000-0000-0000-0000-000000000001"
MSG_ID = "msg-1"


def _result_json(confidence: float) -> dict[str, Any]:
    return {
        "intent": "new_lead",
        "summary_one_line": "summary",
        "language": "en",
        "person_name": "Sara",
        "company_name": "Acme",
        "confidence_overall": confidence,
        "confidence_intent": confidence,
        "confidence_person": confidence,
        "confidence_company": confidence,
    }


class _ScriptedProvider:
    def __init__(self, model_id: str, responses: list[dict[str, Any]]) -> None:
        self.model_id = model_id
        self._responses = responses
        self._i = 0

    def complete_json(self, value: Any) -> dict[str, Any]:
        r = self._responses[self._i]
        self._i += 1
        return r


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def write(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


class _FakeInbox:
    def __init__(self) -> None:
        self.drafts: list[InboxItemDraft] = []

    def create(self, draft: InboxItemDraft) -> None:
        self.drafts.append(draft)


def _message() -> MessageEnvelope:
    return MessageEnvelope(
        external_id="wamid.A",
        thread_id="966500000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=MessageType.TEXT,
        body_text="Need a quote",
        received_at=datetime.now(UTC),
    )


def _orchestrator(
    confidence: float,
    *,
    rules: list[Rule] | None = None,
    candidates: list[CrmRecord] | None = None,
    invalid_twice: bool = False,
) -> tuple[Orchestrator, _FakeAudit, _FakeInbox]:
    responses = [{"bad": 1}, {"bad": 2}] if invalid_twice else [_result_json(confidence)]
    classifier = Classifier(
        _ScriptedProvider("cheap", responses),
        _ScriptedProvider("big", [_result_json(confidence)]),
    )
    audit = _FakeAudit()
    inbox = _FakeInbox()
    orch = Orchestrator(
        classifier,
        audit,
        inbox,
        rules_provider=lambda _t: rules or [],
        crm_lookup=lambda _t, _c: candidates or [],
    )
    return orch, audit, inbox


def test_high_confidence_auto_routes() -> None:
    orch, audit, inbox = _orchestrator(0.95)
    outcome = orch.process(TENANT, MSG_ID, _message())
    assert outcome.action is RoutingAction.AUTO_ROUTE
    assert outcome.band is ConfidenceBand.HIGH
    assert inbox.drafts[0].status is InboxStatus.AUTO_ROUTED
    assert audit.entries[0].action == "auto_routed"
    assert audit.entries[0].actor == "bot"


def test_medium_confidence_pings_control_chat() -> None:
    orch, _audit, inbox = _orchestrator(0.6)
    outcome = orch.process(TENANT, MSG_ID, _message())
    assert outcome.action is RoutingAction.CONTROL_PING
    assert inbox.drafts[0].status is InboxStatus.PENDING


def test_low_confidence_goes_to_inbox() -> None:
    orch, _audit, inbox = _orchestrator(0.3)
    outcome = orch.process(TENANT, MSG_ID, _message())
    assert outcome.action is RoutingAction.INBOX_REVIEW
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW


def test_unclear_after_two_invalid_outputs() -> None:
    orch, audit, inbox = _orchestrator(0.0, invalid_twice=True)
    outcome = orch.process(TENANT, MSG_ID, _message())
    assert outcome.is_unclear is True
    assert outcome.action is RoutingAction.INBOX_REVIEW
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW
    assert audit.entries[0].action == "unclassified"


def test_matching_rule_auto_routes_regardless_of_band() -> None:
    rule = Rule(
        id="r1",
        name="new senders",
        conditions=[SenderIsNew()],
        action=RuleAction(destination_id="dest-9"),
    )
    # Medium confidence, but a rule matches a new sender → auto-route to its destination.
    orch, audit, inbox = _orchestrator(0.6, rules=[rule])
    outcome = orch.process(TENANT, MSG_ID, _message())
    assert outcome.action is RoutingAction.AUTO_ROUTE
    assert outcome.matched_rule_id == "r1"
    assert outcome.destination_id == "dest-9"
    assert audit.entries[0].destination_id == "dest-9"


def test_known_contact_resolves_to_merge_and_is_not_new() -> None:
    known = CrmRecord(external_record_id="c1", name="Sara", phones=["+966500000000"])
    new_sender_rule = Rule(
        id="r1",
        name="new senders only",
        conditions=[SenderIsNew()],
        action=RuleAction(destination_id="dest-9"),
    )
    # Phone matches a cached record → MERGE, sender not new → the new-sender rule must NOT fire.
    orch, _audit, _inbox = _orchestrator(0.95, rules=[new_sender_rule], candidates=[known])
    outcome = orch.process(TENANT, MSG_ID, _message())
    assert outcome.identity_decision is IdentityDecision.MERGE
    assert outcome.matched_rule_id is None
    assert outcome.action is RoutingAction.AUTO_ROUTE  # by HIGH band, not by rule


def test_media_message_is_enriched_before_classify() -> None:
    class _Downloader:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def download(self, tenant_id: str, media_id: str) -> bytes:
            self.calls.append((tenant_id, media_id))
            return b"x"

    class _Transcriber:
        def transcribe(self, audio: bytes, mime: str | None) -> str:
            return "transcribed"

    class _Vision:
        def extract_text(self, document: bytes, mime: str | None) -> str:
            return "ocr"

    from apps.api.media.pipeline import MediaPipeline

    downloader = _Downloader()
    media = MediaPipeline(downloader, _Transcriber(), _Vision())
    classifier = Classifier(
        _ScriptedProvider("cheap", [_result_json(0.95)]),
        _ScriptedProvider("big", [_result_json(0.95)]),
    )
    orch = Orchestrator(
        classifier,
        _FakeAudit(),
        _FakeInbox(),
        rules_provider=lambda _t: [],
        crm_lookup=lambda _t, _c: [],
        media=media,
    )
    voice = MessageEnvelope(
        external_id="wamid.V",
        thread_id="966500000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=MessageType.AUDIO,
        media_id="m1",
        received_at=datetime.now(UTC),
    )
    orch.process(TENANT, MSG_ID, voice)
    assert downloader.calls == [(TENANT, "m1")]  # media pipeline ran before classify


def test_incoming_contact_built_from_classification() -> None:
    # Guards the wiring: the resolver receives the classified person/company, not raw text.
    captured: list[IncomingContact] = []
    classifier = Classifier(
        _ScriptedProvider("cheap", [_result_json(0.95)]),
        _ScriptedProvider("big", [_result_json(0.95)]),
    )

    def _lookup(_t: str, contact: IncomingContact) -> list[CrmRecord]:
        captured.append(contact)
        return []

    orch = Orchestrator(
        classifier,
        _FakeAudit(),
        _FakeInbox(),
        rules_provider=lambda _t: [],
        crm_lookup=_lookup,
    )
    orch.process(TENANT, MSG_ID, _message())
    assert captured[0].name == "Sara"
    assert captured[0].company == "Acme"
    assert captured[0].phone_e164 == "+966500000000"


class _VocabResult:
    """A classification result whose intent is a vocabulary name (not an IntentType enum)."""

    def __init__(self, intent: str, confidence: float) -> None:
        self.intent = intent
        self.summary_one_line = "summary"
        self.language = "en"
        self.person_name = "Sara"
        self.company_name = "Acme"
        self.confidence_overall = confidence
        self.confidence_intent = confidence
        self.confidence_person = confidence
        self.confidence_company = confidence

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "summary_one_line": self.summary_one_line,
            "language": self.language,
            "person_name": self.person_name,
            "company_name": self.company_name,
            "confidence_overall": self.confidence_overall,
            "confidence_intent": self.confidence_intent,
            "confidence_person": self.confidence_person,
            "confidence_company": self.confidence_company,
        }


class _FixedClassifier:
    """A classifier that returns a fixed outcome without calling any LLM provider."""

    def __init__(self, result: _VocabResult) -> None:
        self._result = result

    def classify(self, value: Any) -> ClassificationOutcome:
        return ClassificationOutcome(
            result=self._result,  # type: ignore[arg-type]
            model_used="test",
            escalated=False,
            attempts=1,
        )


def _orchestrator_with_receptionist(
    intent: str,
    confidence: float,
) -> tuple[Orchestrator, _FakeAudit, _FakeInbox]:
    classifier = _FixedClassifier(_VocabResult(intent, confidence))
    audit = _FakeAudit()
    inbox = _FakeInbox()
    orch = Orchestrator(
        classifier,  # type: ignore[arg-type]
        audit,
        inbox,
        rules_provider=lambda _t: [],
        crm_lookup=lambda _t, _c: [],
        receptionist=receptionist_handle,
    )
    return orch, audit, inbox


def test_acting_intent_returns_receptionist_reply() -> None:
    orch, audit, inbox = _orchestrator_with_receptionist("property_question", 0.95)
    outcome = orch.process(TENANT_UUID, MSG_ID, _message())
    assert outcome.action is RoutingAction.RECEPTIONIST_REPLY
    assert outcome.autonomy == "act"
    assert outcome.outbound_action is not None
    assert audit.entries[0].action == "receptionist_reply"


def test_act_and_notify_returns_receptionist_reply_with_notify() -> None:
    orch, audit, inbox = _orchestrator_with_receptionist("booking_enquiry", 0.95)
    outcome = orch.process(TENANT_UUID, MSG_ID, _message())
    assert outcome.action is RoutingAction.RECEPTIONIST_REPLY
    assert outcome.autonomy == "act_and_notify"
    assert outcome.outbound_action is not None


def test_hand_off_intent_falls_through_to_routing() -> None:
    orch, audit, inbox = _orchestrator_with_receptionist("cancel_reservation", 0.95)
    outcome = orch.process(TENANT_UUID, MSG_ID, _message())
    assert outcome.action is not RoutingAction.RECEPTIONIST_REPLY
    assert outcome.autonomy is None


def test_no_receptionist_uses_old_path() -> None:
    orch, _audit, _inbox = _orchestrator(0.95)
    outcome = orch.process(TENANT, MSG_ID, _message())
    assert outcome.action is RoutingAction.AUTO_ROUTE
    assert outcome.autonomy is None
    assert outcome.outbound_action is None
