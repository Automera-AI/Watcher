"""Queue/worker wiring: consumer reloads the persisted message and drives the orchestrator (§5)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks

from apps.api.audit.log import AuditEntry
from apps.api.classifier.service import Classifier
from apps.api.ingestion.service import IngestionService
from apps.api.orchestration.ports import InboxItemDraft
from apps.api.orchestration.queue import (
    BackgroundTasksQueue,
    InlineClassificationQueue,
    LoadedMessage,
    MessageConsumer,
)
from apps.api.orchestration.worker import Orchestrator, RoutingAction
from apps.api.schemas.enums import MessageType, SourceKind
from apps.api.schemas.message import MessageEnvelope

TENANT = "tenant-1"


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
    def __init__(self, model_id: str, response: dict[str, Any]) -> None:
        self.model_id = model_id
        self._response = response

    def complete_json(self, value: Any) -> dict[str, Any]:
        return self._response


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


class _MemoryStore:
    """Doubles as the ingestion ``MessageRepository`` and the worker ``MessageLoader``."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], LoadedMessage] = {}

    def exists(self, tenant_id: str, wa_message_id: str) -> bool:
        return (tenant_id, wa_message_id) in self._rows

    def save(self, tenant_id: str, message: MessageEnvelope) -> None:
        self._rows[(tenant_id, message.wa_message_id)] = LoadedMessage(
            message_id=str(uuid.uuid4()), message=message
        )

    def load(self, tenant_id: str, wa_message_id: str) -> LoadedMessage | None:
        return self._rows.get((tenant_id, wa_message_id))


def _message(wa_id: str = "wamid.A") -> MessageEnvelope:
    return MessageEnvelope(
        wa_message_id=wa_id,
        wa_chat_id="966500000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=MessageType.TEXT,
        body_text="Need a quote",
        received_at=datetime.now(UTC),
    )


def _orchestrator(confidence: float) -> tuple[Orchestrator, _FakeInbox]:
    classifier = Classifier(
        _ScriptedProvider("cheap", _result_json(confidence)),
        _ScriptedProvider("big", _result_json(confidence)),
    )
    inbox = _FakeInbox()
    orch = Orchestrator(
        classifier,
        _FakeAudit(),
        inbox,
        rules_provider=lambda _t: [],
        crm_lookup=lambda _t, _c: [],
    )
    return orch, inbox


def test_consumer_reloads_persisted_message_and_orchestrates() -> None:
    store = _MemoryStore()
    store.save(TENANT, _message())
    orch, inbox = _orchestrator(0.95)
    consumer = MessageConsumer(store, orch)

    outcome = consumer.consume(TENANT, "wamid.A")

    assert outcome is not None
    assert outcome.action is RoutingAction.AUTO_ROUTE
    assert len(inbox.drafts) == 1
    # The orchestrator was handed the persistent id from the loaded row, not the wa id.
    loaded = store.load(TENANT, "wamid.A")
    assert loaded is not None
    assert inbox.drafts[0].message_id == loaded.message_id


def test_consumer_skips_and_logs_when_message_missing(
    caplog: Any,
) -> None:
    orch, inbox = _orchestrator(0.95)
    consumer = MessageConsumer(_MemoryStore(), orch)

    with caplog.at_level(logging.WARNING):
        outcome = consumer.consume(TENANT, "wamid.ghost")

    assert outcome is None
    assert not inbox.drafts
    assert "not found" in caplog.text


def test_background_tasks_queue_defers_until_tasks_run() -> None:
    store = _MemoryStore()
    store.save(TENANT, _message())
    orch, inbox = _orchestrator(0.95)
    tasks = BackgroundTasks()
    queue = BackgroundTasksQueue(MessageConsumer(store, orch), tasks)

    queue.enqueue(TENANT, "wamid.A")
    assert not inbox.drafts  # nothing runs until the response is sent

    asyncio.run(tasks())
    assert len(inbox.drafts) == 1


def test_inline_queue_consumes_immediately() -> None:
    store = _MemoryStore()
    store.save(TENANT, _message())
    orch, inbox = _orchestrator(0.95)
    queue = InlineClassificationQueue(MessageConsumer(store, orch))

    queue.enqueue(TENANT, "wamid.A")
    assert len(inbox.drafts) == 1


def test_ingestion_to_orchestration_end_to_end() -> None:
    # ingest (persist + enqueue) → background tasks run → orchestrated inbox draft: the §5 path.
    store = _MemoryStore()
    orch, inbox = _orchestrator(0.95)
    tasks = BackgroundTasks()
    queue = BackgroundTasksQueue(MessageConsumer(store, orch), tasks)
    service = IngestionService(store, queue)

    result = service.ingest(TENANT, [_message("wamid.1"), _message("wamid.1"), _message("wamid.2")])

    assert (result.accepted, result.duplicates) == (2, 1)
    assert not inbox.drafts  # deferred to background tasks
    asyncio.run(tasks())
    assert len(inbox.drafts) == 2
