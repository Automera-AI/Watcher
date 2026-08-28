"""Tests for the arq worker's composition root (roadmap B5).

Importing ``apps.api.worker`` itself is the first assertion in this file: if the module needed
``REDIS_URL`` or ``DATABASE_URL`` to be set just to load, collecting this file in an unconfigured
CI runner would already have failed before any test body ran.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from arq.connections import RedisSettings
from packages.intents.schema import Vocabulary

from apps.api import worker
from apps.api.audit.log import AuditEntry
from apps.api.classifier.service import Classifier
from apps.api.core.config import Settings
from apps.api.orchestration.ports import InboxItemDraft
from apps.api.orchestration.queue import LoadedMessage, MessageConsumer
from apps.api.orchestration.worker import Orchestrator, RoutingAction
from apps.api.schemas.enums import MessageType, SourceKind
from apps.api.schemas.message import MessageEnvelope

TENANT = "tenant-1"


class _ScriptedProvider:
    def __init__(self, model_id: str, response: dict[str, Any]) -> None:
        self.model_id = model_id
        self._response = response

    def complete_json(self, value: Any) -> dict[str, Any]:
        return self._response


class _FakeAudit:
    def write(self, entry: AuditEntry) -> None:
        pass


class _FakeInbox:
    def __init__(self) -> None:
        self.drafts: list[InboxItemDraft] = []

    def create(self, draft: InboxItemDraft) -> None:
        self.drafts.append(draft)


class _MemoryLoader:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], LoadedMessage] = {}

    def save(self, tenant_id: str, external_id: str, message: MessageEnvelope) -> None:
        self._rows[(tenant_id, external_id)] = LoadedMessage(
            message_id=str(uuid.uuid4()), message=message
        )

    def load(self, tenant_id: str, external_id: str) -> LoadedMessage | None:
        return self._rows.get((tenant_id, external_id))


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


def test_worker_settings_is_importable_without_any_environment() -> None:
    """No ``REDIS_URL``/``DATABASE_URL`` was set above — collecting this module is the proof."""
    assert isinstance(worker.WorkerSettings.redis_settings, RedisSettings)
    assert worker.WorkerSettings.functions == (worker.consume_message,)
    assert worker.WorkerSettings.on_startup is worker.startup
    assert worker.WorkerSettings.on_shutdown is worker.shutdown


def test_consume_message_reuses_the_shared_consumer() -> None:
    """The job function is a thin wrapper: reload, then the same ``Orchestrator`` path (B5)."""
    loader = _MemoryLoader()
    loader.save(TENANT, "wamid.A", _message())
    confidence = 0.95
    result = {
        "intent": "availability_check",
        "summary_one_line": "summary",
        "language": "en",
        "person_name": "Sara",
        "company_name": "Acme",
        "confidence_overall": confidence,
        "confidence_intent": confidence,
        "confidence_person": confidence,
        "confidence_company": confidence,
    }
    classifier = Classifier(_ScriptedProvider("cheap", result), _ScriptedProvider("big", result))
    inbox = _FakeInbox()
    orchestrator = Orchestrator(classifier, _FakeAudit(), inbox, crm_lookup=lambda _t, _c: [])
    ctx: dict[str, Any] = {"consumer": MessageConsumer(loader, orchestrator)}

    outcome = asyncio.run(worker.consume_message(ctx, TENANT, "wamid.A"))

    assert outcome is not None
    assert outcome.action is RoutingAction.CONTROL_PING
    assert len(inbox.drafts) == 1


def test_startup_passes_one_selected_clinic_vocabulary_to_both_graphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, tenant_vertical="clinics")
    seen: dict[str, Any] = {}
    database = SimpleNamespace()
    classifier = SimpleNamespace()
    graph = SimpleNamespace(sender=None, consumer=SimpleNamespace())

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "build_database", lambda _settings: database)

    def fake_classifier(_settings: Settings, *, vocabulary: Vocabulary) -> Any:
        seen["classifier"] = vocabulary
        return classifier

    def fake_consumer(
        _settings: Settings,
        _database: Any,
        _classifier: Any,
        *,
        vocabulary: Vocabulary,
    ) -> Any:
        seen["consumer"] = vocabulary
        return graph

    monkeypatch.setattr(worker, "build_classifier", fake_classifier)
    monkeypatch.setattr(worker, "build_consumer", fake_consumer)
    ctx: dict[str, Any] = {}

    asyncio.run(worker.startup(ctx))

    assert seen["classifier"] is seen["consumer"]
    assert seen["classifier"].vertical == "clinics"
    assert ctx["consumer"] is graph.consumer


def test_shutdown_closes_sender_and_disposes_database_when_present() -> None:
    events: list[str] = []

    class _FakeSender:
        def close(self) -> None:
            events.append("sender.close")

    class _FakeDatabase:
        def dispose(self) -> None:
            events.append("database.dispose")

    ctx: dict[str, Any] = {"sender": _FakeSender(), "database": _FakeDatabase()}

    asyncio.run(worker.shutdown(ctx))

    assert events == ["sender.close", "database.dispose"]


def test_shutdown_tolerates_a_process_with_no_sender_configured() -> None:
    # A worker started with no WHATSAPP_* credentials still has a database to close — the same
    # `sender is not None` guard `main.py`'s own `_shutdown` uses.
    ctx: dict[str, Any] = {"sender": None, "database": None}

    asyncio.run(worker.shutdown(ctx))  # must not raise
