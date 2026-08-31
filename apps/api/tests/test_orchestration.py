"""Tests for the orchestrator decision tree (addendum §5 → §12, roadmap A5/A6).

Two things changed shape here in A5 and the tests changed with them. Rules and destinations are
gone from this path — the assertions that a matching rule auto-routed a message went with them,
because there is nothing left to route to — and the receptionist path is no longer a special case
that a few tests poke at, but the ordinary way a classified message is handled.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from packages.intents.schema import (
    Vocabulary,
    default_vocabulary,
    shipped_vocabularies,
    vocabulary_for,
)

from apps.api.audit.log import AuditEntry
from apps.api.classifier.service import Classifier
from apps.api.conversations.receptionist import handle as receptionist_handle
from apps.api.conversations.task import (
    AWAITING_ANOTHER_DATE_SLOT,
    NON_PROGRESS_TURNS_SLOT,
    Task,
    TaskStatus,
)
from apps.api.conversations.tools import REGISTRY, AnswerFromKnowledge
from apps.api.core.alerts import AlertOutcome, EmergencyAlert
from apps.api.core.emergency import EMERGENCY_REPLY
from apps.api.core.knowledge import Fact
from apps.api.core.policy import DEFAULT_POLICY, TenantPolicy
from apps.api.identity.models import CrmRecord
from apps.api.identity.resolver import IncomingContact
from apps.api.orchestration.ports import (
    ClassificationDraft,
    ConversationState,
    InboxItemDraft,
)
from apps.api.orchestration.worker import Orchestrator, ProcessOutcome, RoutingAction
from apps.api.schemas.enums import (
    ConfidenceBand,
    IdentityDecision,
    InboxStatus,
    MessageType,
    SourceKind,
)
from apps.api.schemas.envelope import InboundTurn, OutboundAction
from apps.api.schemas.message import MessageEnvelope

TENANT = "tenant-1"
TENANT_UUID = "00000000-0000-0000-0000-000000000001"
MSG_ID = "msg-1"


def _result_json(
    confidence: float,
    intent: str = "availability_check",
    slots: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "summary_one_line": "summary",
        "language": "en",
        "person_name": "Sara",
        "company_name": "Acme",
        "extracted_slots": slots or {},
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
        r = self._responses[min(self._i, len(self._responses) - 1)]
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


class _FakeClassifications:
    def __init__(self) -> None:
        self.drafts: list[ClassificationDraft] = []

    def record(self, draft: ClassificationDraft) -> str | None:
        self.drafts.append(draft)
        return f"classification-{len(self.drafts)}"


class _FakeConversations:
    """An in-memory conversation store: one conversation, one task, a count of replies."""

    def __init__(self, task: Task | None = None, replies_sent: int = 0) -> None:
        self.task = task
        self.replies_sent = replies_sent
        self.inbound: list[InboundTurn] = []
        self.replies: list[OutboundAction] = []

    def begin(self, turn: InboundTurn) -> ConversationState:
        self.inbound.append(turn)
        return ConversationState(
            conversation_id="conversation-1", task=self.task, replies_sent=self.replies_sent
        )

    def record_reply(
        self,
        state: ConversationState,
        turn: InboundTurn,
        task: Task | None,
        action: OutboundAction,
    ) -> None:
        # `None` is the emergency path (G3): the reply belongs to the transcript and to no job,
        # and the task already in flight is left exactly as it was.
        if task is not None:
            self.task = task
        self.replies.append(action)
        self.replies_sent += 1


class _FakeSender:
    def __init__(self, *, fails: bool = False) -> None:
        self.sent: list[tuple[OutboundAction, InboundTurn]] = []
        self._fails = fails

    async def send(self, action: OutboundAction, turn: InboundTurn) -> None:
        if self._fails:
            raise RuntimeError("channel unreachable")
        self.sent.append((action, turn))

    def close(self) -> None:
        return None


class _FakeKnowledge:
    """A knowledge base with exactly one fact, for tests that need ``property_question`` to
    resolve to an actual answer rather than a hand-off (roadmap 2.4)."""

    def __init__(self, question: str, answer: str = "Yes.") -> None:
        self._fact = Fact(id="1", topic="test", question=question, answer=answer, sensitive=False)

    def search(self, tenant_id: str, property_id: str | None = None) -> list[Fact]:
        return [self._fact]


def _message(text: str = "Need a quote") -> MessageEnvelope:
    return MessageEnvelope(
        external_id="wamid.A",
        thread_id="966500000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=MessageType.TEXT,
        body_text=text,
        received_at=datetime.now(UTC),
    )


def _orchestrator(
    confidence: float,
    *,
    candidates: list[CrmRecord] | None = None,
    invalid_twice: bool = False,
    classifications: _FakeClassifications | None = None,
) -> tuple[Orchestrator, _FakeAudit, _FakeInbox]:
    """An orchestrator with no receptionist — the degraded, file-only pipeline."""
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
        crm_lookup=lambda _t, _c: candidates or [],
        classifications=classifications,
    )
    return orch, audit, inbox


def _run(orch: Orchestrator, tenant: str = TENANT, message: MessageEnvelope | None = None) -> Any:
    return asyncio.run(orch.process(tenant, MSG_ID, message or _message()))


def test_medium_confidence_pings_control_chat() -> None:
    orch, _audit, inbox = _orchestrator(0.6)
    outcome = _run(orch)
    assert outcome.action is RoutingAction.CONTROL_PING
    assert inbox.drafts[0].status is InboxStatus.PENDING


def test_high_confidence_without_a_receptionist_still_only_pings() -> None:
    """A5's excision, asserted: confidence is not a destination.

    This used to auto-route to whatever the rules said. With the v1 filer gone there is nothing to
    route *to*, so a message nobody is going to answer reaches a person instead.
    """
    orch, audit, inbox = _orchestrator(0.95)
    outcome = _run(orch)
    assert outcome.action is RoutingAction.CONTROL_PING
    assert outcome.band is ConfidenceBand.HIGH
    assert audit.entries[0].action == "control_ping"
    assert audit.entries[0].actor == "bot"
    assert inbox.drafts[0].status is InboxStatus.PENDING


def test_low_confidence_goes_to_inbox() -> None:
    orch, _audit, inbox = _orchestrator(0.3)
    outcome = _run(orch)
    assert outcome.action is RoutingAction.INBOX_REVIEW
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW


def test_unclear_after_two_invalid_outputs() -> None:
    classifications = _FakeClassifications()
    orch, audit, inbox = _orchestrator(0.0, invalid_twice=True, classifications=classifications)
    outcome = _run(orch)
    assert outcome.is_unclear is True
    assert outcome.action is RoutingAction.INBOX_REVIEW
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW
    assert audit.entries[0].action == "unclassified"
    # Nothing to describe, so no classification row and nothing for the inbox item to point at.
    assert classifications.drafts == []
    assert inbox.drafts[0].classification_id is None


def test_the_classification_is_recorded_with_its_telemetry() -> None:
    """``classifications`` stops being a table nothing writes to (A5)."""
    classifications = _FakeClassifications()
    orch, _audit, inbox = _orchestrator(0.95, classifications=classifications)
    outcome = _run(orch)

    draft = classifications.drafts[0]
    assert draft.model_used == "cheap"
    assert draft.prompt_version  # carried from the classifier, not invented here
    assert draft.latency_ms >= 0
    assert draft.result.person_name == "Sara"
    # The inbox item points at the row, so the control page can show why it was filed this way.
    assert inbox.drafts[0].classification_id == outcome.classification_id == "classification-1"


def test_known_contact_resolves_to_merge() -> None:
    known = CrmRecord(external_record_id="c1", name="Sara", phones=["+966500000000"])
    orch, _audit, _inbox = _orchestrator(0.95, candidates=[known])
    outcome = _run(orch)
    assert outcome.identity_decision is IdentityDecision.MERGE


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
    asyncio.run(orch.process(TENANT, MSG_ID, voice))
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

    orch = Orchestrator(classifier, _FakeAudit(), _FakeInbox(), crm_lookup=_lookup)
    asyncio.run(orch.process(TENANT, MSG_ID, _message()))
    assert captured[0].name == "Sara"
    assert captured[0].company == "Acme"
    assert captured[0].phone_e164 == "+966500000000"


# ── The receptionist path ──────────────────────────────────────────────────────────────────


def _conversing(
    intent: str,
    confidence: float,
    *,
    conversations: _FakeConversations | None = None,
    sender: _FakeSender | None = None,
    alerter: _FakeAlerter | None = None,
    slots: dict[str, str] | None = None,
    vocabulary: Vocabulary | None = None,
    policy: TenantPolicy | None = None,
) -> tuple[Orchestrator, _FakeAudit, _FakeInbox, _FakeConversations]:
    classifier = Classifier(
        _ScriptedProvider("cheap", [_result_json(confidence, intent, slots)]),
        _ScriptedProvider("big", [_result_json(confidence, intent, slots)]),
    )
    audit = _FakeAudit()
    inbox = _FakeInbox()
    store = conversations or _FakeConversations()
    orch = Orchestrator(
        classifier,
        audit,
        inbox,
        crm_lookup=lambda _t, _c: [],
        receptionist=receptionist_handle,
        conversations=store,
        sender=sender,
        alerter=alerter,
        vocabulary=vocabulary or default_vocabulary(),
        policy=policy if policy is not None else DEFAULT_POLICY,
    )
    return orch, audit, inbox, store


def _converse(orch: Orchestrator, text: str = "Need a quote") -> ProcessOutcome:
    return asyncio.run(orch.process(TENANT_UUID, MSG_ID, _message(text)))


def test_acting_intent_returns_receptionist_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        REGISTRY, "answer_from_knowledge", AnswerFromKnowledge(_FakeKnowledge("Need a quote"))
    )
    orch, audit, _inbox, store = _conversing("property_question", 0.95)
    outcome = _converse(orch)
    assert outcome.action is RoutingAction.RECEPTIONIST_REPLY
    assert outcome.autonomy == "act"
    assert outcome.outbound_action is not None
    assert audit.entries[0].action == "receptionist_reply"
    assert store.replies[0] is outcome.outbound_action


def test_the_inbound_turn_is_recorded_before_the_receptionist_answers() -> None:
    """Continuity begins with a transcript. Without it there is nothing to be continuous with."""
    orch, _audit, _inbox, store = _conversing("property_question", 0.95)
    _converse(orch, "Is there parking?")
    assert [turn.text for turn in store.inbound] == ["Is there parking?"]
    assert store.inbound[0].channel_identity == "+966500000000"


def test_a_hand_off_intent_still_answers_the_guest_and_files_for_a_person() -> None:
    """The v1 behaviour was silence plus a filed row. Both halves are now true at once."""
    orch, audit, inbox, store = _conversing("cancel_reservation", 0.95)
    outcome = _converse(orch)

    assert outcome.action is RoutingAction.HANDOFF
    assert outcome.autonomy == "hand_off"
    assert outcome.outbound_action is not None
    assert outcome.outbound_action.kind == "handoff"
    assert audit.entries[0].action == "handed_off"
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW
    assert store.task is not None and store.task.status is TaskStatus.HANDED_OFF


def test_an_active_task_is_continued_rather_than_restarted() -> None:
    """The point of A5: the second message resumes the job the first one opened."""
    running = Task(intent="availability_check", slots={"check_in": "4 June"})
    store = _FakeConversations(task=running)
    orch, _audit, _inbox, _store = _conversing("availability_check", 0.95, conversations=store)

    _converse(orch, "and for two guests")

    assert store.task is running  # same job, not a new one
    assert store.task.slots["check_in"] == "4 June"  # what the previous turn learned survived


def test_a_task_that_stops_making_progress_fetches_a_person() -> None:
    """The clarifying-turn budget, honoured end to end.

    Without this a task that cannot be filled asks the same question on every message, forever,
    which is the failure mode continuity introduces and the vocabulary already had an answer for.
    """
    store = _FakeConversations(task=Task(intent="availability_check"), replies_sent=3)
    orch, audit, inbox, _store = _conversing("availability_check", 0.95, conversations=store)

    outcome = _converse(orch)

    assert outcome.action is RoutingAction.HANDOFF
    assert outcome.outbound_action is not None
    assert outcome.outbound_action.kind == "handoff"
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW


def test_the_reply_is_put_on_the_wire() -> None:
    """A6, in one assertion: the composed reply actually leaves the process."""
    sender = _FakeSender()
    orch, _audit, _inbox, _store = _conversing("property_question", 0.95, sender=sender)
    outcome = _converse(orch)

    action, turn = sender.sent[0]
    assert action is outcome.outbound_action
    assert turn.channel_identity == "+966500000000"  # sent back to whoever asked
    assert outcome.delivered is True


def test_a_send_failure_does_not_lose_the_message() -> None:
    """A transient failure at the last step must not undo the four that succeeded."""
    sender = _FakeSender(fails=True)
    orch, audit, inbox, store = _conversing("property_question", 0.95, sender=sender)

    outcome = _converse(orch)

    assert outcome.delivered is False
    assert store.replies  # the reply is recorded, so the next turn knows we spoke
    assert audit.entries and inbox.drafts  # and the decision is still filed


def test_without_a_sender_the_reply_is_composed_and_recorded_but_undelivered() -> None:
    """The half-configured deploy: everything works except the wire."""
    orch, _audit, _inbox, store = _conversing("property_question", 0.95)
    outcome = _converse(orch)
    assert outcome.delivered is None
    assert store.replies


def test_a_receptionist_without_a_conversation_store_is_refused() -> None:
    """The trap A4 left a note about, made structural.

    A receptionist with no store forgets the previous turn on every message, which looks like it
    works and is the exact thing A5 exists to remove. It is refused at construction rather than
    discovered on the second message of a real conversation.
    """
    import pytest

    classifier = Classifier(
        _ScriptedProvider("cheap", [_result_json(0.95)]),
        _ScriptedProvider("big", [_result_json(0.95)]),
    )
    with pytest.raises(ValueError, match="configured together"):
        Orchestrator(
            classifier,
            _FakeAudit(),
            _FakeInbox(),
            crm_lookup=lambda _t, _c: [],
            receptionist=receptionist_handle,
        )


def test_tenant_id_reaches_the_turn_as_a_uuid() -> None:
    orch, _audit, _inbox, store = _conversing("property_question", 0.95)
    _converse(orch)
    assert store.inbound[0].tenant_id == uuid.UUID(TENANT_UUID)


# ── The emergency path (roadmap G3) ────────────────────────────────────────────────────────


class _FakeAlerter:
    def __init__(self, *, delivered: bool = True, channel: str = "whatsapp_text") -> None:
        self.alerts: list[EmergencyAlert] = []
        self._outcome = AlertOutcome(delivered=delivered, channel=channel)

    async def alert(self, alert: EmergencyAlert) -> AlertOutcome:
        self.alerts.append(alert)
        return self._outcome


class _ExplodingProvider:
    """A model that must not be called. An emergency is answered without one."""

    model_id = "must-not-be-called"

    def complete_json(self, value: Any) -> dict[str, Any]:
        raise AssertionError("the classifier ran on an emergency")


def _emergency_orchestrator(
    *,
    alerter: _FakeAlerter | None = None,
    sender: _FakeSender | None = None,
    conversations: _FakeConversations | None = None,
    with_receptionist: bool = True,
    vocabulary: Vocabulary | None = None,
) -> tuple[Orchestrator, _FakeAudit, _FakeInbox, _FakeConversations | None]:
    """An orchestrator whose classifier raises if anything reaches it."""
    selected = vocabulary or default_vocabulary()
    classifier = Classifier(_ExplodingProvider(), _ExplodingProvider())
    audit, inbox = _FakeAudit(), _FakeInbox()
    store = (conversations or _FakeConversations()) if with_receptionist else None
    orch = Orchestrator(
        classifier,
        audit,
        inbox,
        crm_lookup=lambda _t, _c: [],
        receptionist=receptionist_handle if with_receptionist else None,
        conversations=store,
        sender=sender,
        alerter=alerter,
        vocabulary=selected,
    )
    return orch, audit, inbox, store


def test_an_emergency_is_answered_without_ever_being_classified() -> None:
    """The ordering *is* the item: nothing may stand between a gas leak and an operator.

    The classifier here raises on any call. A model is a network round trip that can be slow,
    wrong or down, and the vocabulary has said "checked before intent, before confidence, before
    anything" since item 0.3.
    """
    alerter = _FakeAlerter()
    orch, audit, inbox, store = _emergency_orchestrator(alerter=alerter)

    outcome = _converse(orch, "hi, there is a smell of gas in the kitchen")

    assert outcome.action is RoutingAction.EMERGENCY
    assert outcome.emergency is not None and outcome.emergency.trigger_id == "gas"
    assert outcome.autonomy == "hand_off"
    assert outcome.classification_id is None  # nothing was classified, so nothing to point at
    assert audit.entries[0].action == "emergency"
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW
    assert inbox.drafts[0].band is ConfidenceBand.HIGH
    assert inbox.drafts[0].snapshot["trigger_id"] == "gas"
    assert store is not None and store.replies  # the guest was answered, and it is on the record


def test_a_clinic_emergency_uses_the_selected_vocabulary_through_the_orchestrator() -> None:
    clinics = shipped_vocabularies()["clinics"]
    orch, audit, inbox, _store = _emergency_orchestrator(alerter=_FakeAlerter(), vocabulary=clinics)

    outcome = _converse(orch, "جلدي اتحرق جامد بعد الليزر")

    assert outcome.action is RoutingAction.EMERGENCY
    assert outcome.emergency is not None and outcome.emergency.trigger_id == "burn"
    assert audit.entries[0].action == "emergency"
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW


def test_clinic_emergency_log_omits_patient_text_and_phone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clinics = shipped_vocabularies()["clinics"]
    alerter = _FakeAlerter()
    orch, _audit, _inbox, _store = _emergency_orchestrator(alerter=alerter, vocabulary=clinics)
    symptom = "جلدي اتحرق جامد بعد الليزر"

    with caplog.at_level(logging.CRITICAL, logger="apps.api.orchestration.worker"):
        _converse(orch, symptom)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "trigger=burn" in logged
    assert TENANT_UUID in logged
    assert MSG_ID in logged
    assert "جلدي اتحرق" not in logged
    assert "+966500000000" not in logged
    assert alerter.alerts[0].text == symptom
    assert alerter.alerts[0].guest_identity == "+966500000000"


def test_the_guest_is_answered_and_a_person_is_alerted() -> None:
    """Both halves of ``reply_immediately`` + ``alert``, in one message's worth of work."""
    alerter, sender = _FakeAlerter(), _FakeSender()
    orch, _audit, _inbox, _store = _emergency_orchestrator(alerter=alerter, sender=sender)

    outcome = _converse(orch, "FIRE in the building")

    assert outcome.delivered is True
    assert outcome.alerted is True
    action, turn = sender.sent[0]
    assert action.text == EMERGENCY_REPLY
    assert turn.channel_identity == "+966500000000"

    alert = alerter.alerts[0]
    assert alert.trigger_id == "fire"
    assert alert.guest_identity == "+966500000000"
    assert alert.text == "FIRE in the building"  # the operator gets the guest's own words
    # What the vocabulary asked for is carried through, so the gap is visible rather than assumed.
    assert alert.requested_channel == "phone_call_to_operator"


def test_an_emergency_is_still_answered_and_filed_with_no_alerter() -> None:
    """The degraded deploy. Detection does not depend on there being somewhere to send it."""
    sender = _FakeSender()
    orch, audit, inbox, _store = _emergency_orchestrator(alerter=None, sender=sender)

    outcome = _converse(orch, "someone broke in")

    assert outcome.action is RoutingAction.EMERGENCY
    assert outcome.alerted is None  # nothing tried; distinct from "tried and failed"
    assert outcome.delivered is True
    assert audit.entries and inbox.drafts


def test_an_undelivered_alert_is_reported_rather_than_raised() -> None:
    """A failed alert must not cost the guest the reply, and must not be silent."""
    alerter, sender = _FakeAlerter(delivered=False), _FakeSender()
    orch, _audit, inbox, _store = _emergency_orchestrator(alerter=alerter, sender=sender)

    outcome = _converse(orch, "we need an ambulance")

    assert outcome.alerted is False
    assert outcome.delivered is True
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW


def test_an_emergency_is_detected_without_a_receptionist_wired() -> None:
    """The file-only pipeline detects one too. The check is above that branch, not inside it."""
    alerter = _FakeAlerter()
    orch, audit, inbox, _store = _emergency_orchestrator(alerter=alerter, with_receptionist=False)

    outcome = _converse(orch, "the pipe burst, water everywhere")

    assert outcome.action is RoutingAction.EMERGENCY
    assert alerter.alerts[0].trigger_id == "flood"
    assert audit.entries[0].action == "emergency"
    assert inbox.drafts[0].status is InboxStatus.NEEDS_REVIEW


def test_the_job_in_flight_is_left_alone() -> None:
    """A guest with a gas leak may still want their booking answered afterwards."""
    running = Task(intent="availability_check", slots={"check_in": "4 June"})
    store = _FakeConversations(task=running)
    orch, _audit, _inbox, _store = _emergency_orchestrator(
        alerter=_FakeAlerter(), conversations=store
    )

    _converse(orch, "there is a smell of gas")

    assert store.task is running
    assert store.task.slots["check_in"] == "4 June"
    assert store.replies[0].text == EMERGENCY_REPLY  # the emergency reply is in the transcript


def test_an_ordinary_message_still_takes_the_ordinary_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard against the interesting failure: a detector that fires on everything."""
    monkeypatch.setitem(
        REGISTRY,
        "answer_from_knowledge",
        AnswerFromKnowledge(_FakeKnowledge("Is there a fireplace in the living room?")),
    )
    alerter = _FakeAlerter()
    orch, _audit, _inbox, _store = _conversing("property_question", 0.95, alerter=alerter)

    outcome = _converse(orch, "Is there a fireplace in the living room?")

    assert outcome.action is RoutingAction.RECEPTIONIST_REPLY
    assert outcome.emergency is None
    assert alerter.alerts == []


# ── Slot extraction reaching the task (demo step 5) ────────────────────────────────────────


def _clinic_message(text: str, at: datetime) -> MessageEnvelope:
    envelope = _message(text)
    return envelope.model_copy(update={"received_at": at})


def test_what_the_model_extracted_reaches_the_task() -> None:
    """The gap ``worker.py`` carried as ``{}`` since the receptionist was wired.

    Three of ``booking_enquiry``'s four required slots arrive in one message and all three are
    kept — the difference between a booking journey and three clarifying questions ending in a
    hand-off. The fourth, the time, is not asked for here: the receptionist offers the times the
    diary actually holds (step 6), so it is still outstanding at this point by design.
    """
    orch, _audit, _inbox, store = _conversing(
        "booking_enquiry",
        0.95,
        slots={"service": "ليزر", "branch": "الشيخ زايد", "requested_date": "بكرة"},
        vocabulary=vocabulary_for("clinics"),
        policy=TenantPolicy(timezone="Africa/Cairo"),
    )
    asyncio.run(
        orch.process(
            TENANT_UUID,
            MSG_ID,
            _clinic_message(
                "عاوزة أحجز ليزر في الشيخ زايد بكرة", datetime(2026, 8, 31, 12, tzinfo=UTC)
            ),
        )
    )

    assert store.task is not None
    assert store.task.slots["service"] == "ليزر"
    assert store.task.slots["branch"] == "الشيخ زايد"
    # Resolved against the tenant's clock, not the model's sense of the date.
    assert store.task.slots["requested_date"] == "2026-09-01"
    assert store.task.missing == ["requested_time"]


def test_a_slot_the_intent_never_declares_does_not_reach_the_task() -> None:
    """``normalise_slots`` is between the model and the task, and it is not optional."""
    orch, _audit, _inbox, store = _conversing(
        "price_enquiry",
        0.95,
        slots={"service": "فيلر", "patient_age": "34"},
        vocabulary=vocabulary_for("clinics"),
    )
    _converse(orch, "الفيلر بكام؟")
    assert store.task is not None
    assert store.task.slots["service"] == "فيلر"
    assert "patient_age" not in store.task.slots
    assert store.task.slots[NON_PROGRESS_TURNS_SLOT] == "0"


def test_the_date_is_resolved_on_the_tenants_clock_not_the_servers() -> None:
    """23:30 UTC is 01:30 the next day in Cairo, and "tomorrow" moves with the patient."""
    orch, _audit, _inbox, store = _conversing(
        "booking_enquiry",
        0.95,
        slots={"service": "فاشيال", "branch": "المعادي", "requested_date": "بكرة"},
        vocabulary=vocabulary_for("clinics"),
        policy=TenantPolicy(timezone="Africa/Cairo"),
    )
    asyncio.run(
        orch.process(
            TENANT_UUID,
            MSG_ID,
            _clinic_message("احجزيلي بكرة", datetime(2026, 8, 31, 23, 30, tzinfo=UTC)),
        )
    )
    assert store.task is not None
    assert store.task.slots["requested_date"] == "2026-09-02"


def test_a_fabricated_date_never_reaches_task_state_and_the_missing_day_is_asked() -> None:
    """Journey B (demo step 3): an availability follow-up with no date must not invent one.

    An active task already holds service and branch. The patient asks "what times are free?"
    without naming a day, but the classifier emits a ``requested_date`` all the same. The provenance
    guard drops it because this message states no such day, so the task keeps the service and
    branch it had, invents no date, and asks for the missing one — rather than checking a diary
    against a day the patient never chose, or handing off.
    """
    active = Task(
        intent="availability_check",
        slots={"service": "فاشيال", "branch": "المعادي"},
        vocabulary=vocabulary_for("clinics"),
    )
    orch, _audit, _inbox, store = _conversing(
        "availability_check",
        0.95,
        slots={"requested_date": "بكرة"},  # a day the message below never states
        conversations=_FakeConversations(task=active),
        vocabulary=vocabulary_for("clinics"),
        policy=TenantPolicy(timezone="Africa/Cairo"),
    )
    outcome = asyncio.run(
        orch.process(
            TENANT_UUID,
            MSG_ID,
            _clinic_message("المواعيد المتاحة ايه", datetime(2026, 8, 31, 12, tzinfo=UTC)),
        )
    )

    assert store.task is not None
    assert store.task.slots["service"] == "فاشيال"
    assert store.task.slots["branch"] == "المعادي"
    assert "requested_date" not in store.task.slots
    assert outcome.action is not RoutingAction.HANDOFF
    assert outcome.outbound_action is not None
    assert outcome.outbound_action.kind == "ask"


def test_affirmative_after_none_available_cannot_restore_old_date_from_classifier() -> None:
    """The worker's temporal guard runs before the awaiting-another-date continuation."""
    active = Task(
        intent="booking_enquiry",
        slots={
            "service": "فاشيال",
            "branch": "المعادي",
            AWAITING_ANOTHER_DATE_SLOT: "1",
        },
        vocabulary=vocabulary_for("clinics"),
    )
    orch, _audit, _inbox, store = _conversing(
        "booking_enquiry",
        0.95,
        slots={"requested_date": "2026-09-03"},
        conversations=_FakeConversations(task=active),
        vocabulary=vocabulary_for("clinics"),
        policy=TenantPolicy(timezone="Africa/Cairo"),
    )

    outcome = _converse(orch, "تمام")

    assert store.task is not None
    assert "requested_date" not in store.task.slots
    assert AWAITING_ANOTHER_DATE_SLOT not in store.task.slots
    assert store.task.slots["service"] == "فاشيال"
    assert store.task.slots["branch"] == "المعادي"
    assert outcome.outbound_action is not None
    assert outcome.outbound_action.kind == "ask"


@pytest.mark.parametrize(
    "message",
    ["جلسة رقم 6", "6 مناطق", "مساء الخير، عايزة 6 جلسات", "6 جلسات الساعة 8"],
)
def test_a_fabricated_time_from_a_bare_number_never_reaches_task_state(message: str) -> None:
    """Codex blocker, worker path: a classifier emits ``requested_time`` for a non-time number.

    A session ordinal ("جلسة رقم 6"), a number beside a meem-initial word ("6 مناطق"), a stray time
    word licensing an unrelated count ("مساء الخير، عايزة 6 جلسات"), and a mixed message whose real
    time is 08:00 but whose leading count "6" greedily parses to 18:00 ("6 جلسات الساعة 8") each let
    the greedy parser produce a fabricated 18:00. The provenance guard drops it before it reaches
    task state, so an active booking already holding service, branch and date is not silently given
    an appointment time the patient never stated.
    """
    active = Task(
        intent="booking_enquiry",
        slots={"service": "فاشيال", "branch": "المعادي", "requested_date": "2026-09-02"},
        vocabulary=vocabulary_for("clinics"),
    )
    orch, _audit, _inbox, store = _conversing(
        "booking_enquiry",
        0.95,
        slots={"requested_time": "18:00"},  # a time the message below never states
        conversations=_FakeConversations(task=active),
        vocabulary=vocabulary_for("clinics"),
        policy=TenantPolicy(timezone="Africa/Cairo"),
    )
    asyncio.run(
        orch.process(
            TENANT_UUID,
            MSG_ID,
            _clinic_message(message, datetime(2026, 9, 1, 12, tzinfo=UTC)),
        )
    )

    assert store.task is not None
    assert "requested_time" not in store.task.slots


def test_the_model_is_told_todays_date_on_the_tenants_clock() -> None:
    """It has no clock of its own; without this "بكرة" resolves against a training cut."""
    seen: list[Any] = []

    class _Recording(_ScriptedProvider):
        def complete_json(self, value: Any) -> dict[str, Any]:
            seen.append(value)
            return super().complete_json(value)

    classifier = Classifier(
        _Recording("cheap", [_result_json(0.95, "greeting")]),
        _Recording("big", [_result_json(0.95, "greeting")]),
    )
    orch = Orchestrator(
        classifier,
        _FakeAudit(),
        _FakeInbox(),
        crm_lookup=lambda _t, _c: [],
        policy=TenantPolicy(timezone="Africa/Cairo"),
        vocabulary=vocabulary_for("clinics"),
    )
    asyncio.run(
        orch.process(
            TENANT_UUID, MSG_ID, _clinic_message("اهلا", datetime(2026, 8, 31, 23, 30, tzinfo=UTC))
        )
    )
    assert seen and seen[0].local_now is not None
    assert seen[0].local_now.date().isoformat() == "2026-09-01"
