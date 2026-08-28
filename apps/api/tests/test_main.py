"""Tests for the composition root (roadmap A4).

Everything below this file has been tested against doubles: an in-memory repository, a recording
queue, a stub inbox writer. Each of those tests answers "does this unit behave", and none of them
answers the question A4 exists to settle — whether the objects actually fit together. So this file
assembles the real graph, with the real database implementations, over SQLite and one stub model,
and pushes a signed webhook through it: one HTTP request in, rows in ``messages``, ``audit_log``
and ``inbox_items`` out.

The stub is the model and nothing else. The repository, the loader, the queue, the orchestrator,
the tenant resolver and the router are the production objects, wired by the production function.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.channels import ConfigError
from apps.api.classifier.factory import build_classifier
from apps.api.classifier.prompt import PROMPT_VERSION
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import ClassificationInput
from apps.api.core.config import Settings
from apps.api.db.engine import Database
from apps.api.db.models import (
    AuditLogRow,
    ChannelConfig,
    Classification,
    Conversation,
    InboxItem,
    Message,
    TaskRow,
    Tenant,
    Turn,
)
from apps.api.ingestion.security import SIGNATURE_HEADER, expected_signature
from apps.api.main import assemble, create_application
from apps.api.orchestration.queue import RedisClassificationQueue, ThreadPoolClassificationQueue
from apps.api.schemas.enums import ConfidenceBand, InboxStatus

APP_SECRET = "app-secret"
VERIFY_TOKEN = "verify-token"
ENDPOINT_ID = "PNID"
TENANT_ID = str(uuid.uuid4())


class StubProvider:
    """An ``LLMProvider`` that answers with a fixed, valid classification."""

    def __init__(self, model_id: str = "stub-model", confidence: float = 0.95) -> None:
        self.model_id = model_id
        self.confidence = confidence
        self.calls: list[ClassificationInput] = []

    def complete_json(self, value: ClassificationInput) -> dict[str, Any]:
        self.calls.append(value)
        return {
            "intent": "booking_enquiry",
            "summary_one_line": "Guest asks about availability",
            "language": "en",
            "person_name": "Sara",
            "confidence_overall": self.confidence,
            "confidence_intent": self.confidence,
            "confidence_person": self.confidence,
            "confidence_company": self.confidence,
        }


class BlockingProvider(StubProvider):
    """A provider that does not answer until released — a model that is simply slow."""

    def __init__(self) -> None:
        super().__init__(model_id="blocking-model")
        self._released = threading.Event()

    def complete_json(self, value: ClassificationInput) -> dict[str, Any]:
        self._released.wait(timeout=5)
        return super().complete_json(value)

    def release(self) -> None:
        self._released.set()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Configuration built from an explicit environment, never the developer's own."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", VERIFY_TOKEN)
    return Settings(_env_file=None)


@pytest.fixture
def seeded(database: Database) -> Database:
    """A database with one tenant and one configured endpoint — what B1 provisions for real."""
    with database.session() as session:
        session.add(Tenant(id=uuid.UUID(TENANT_ID), name="Acme Stays", tier="saas"))
        session.add(
            ChannelConfig(
                tenant_id=uuid.UUID(TENANT_ID), kind="chat", external_id=ENDPOINT_ID, config={}
            )
        )
    return database


@pytest.fixture
def provider() -> StubProvider:
    return StubProvider()


@pytest.fixture
def app(settings: Settings, seeded: Database, provider: StubProvider) -> FastAPI:
    return assemble(settings, seeded, Classifier(provider, provider))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _payload(external_id: str = "wamid.A", text: str = "Any rooms free in June?") -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": ENDPOINT_ID},
                                "contacts": [
                                    {"profile": {"name": "Sara"}, "wa_id": "966500000000"}
                                ],
                                "messages": [
                                    {
                                        "from": "966500000000",
                                        "id": external_id,
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


def _post(client: TestClient, body: bytes) -> int:
    response = client.post(
        "/webhook", content=body, headers={SIGNATURE_HEADER: expected_signature(APP_SECRET, body)}
    )
    return int(response.status_code)


def _drain(app: FastAPI) -> None:
    """Wait for the classification the webhook handed to the worker pool.

    The webhook answers before classification runs — that is the behaviour under test — so the
    assertions have to wait for the thread the request deliberately did not wait for.
    """
    app.state.queue.shutdown()


def test_health_is_served(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_verify_handshake_uses_the_configured_token(client: TestClient) -> None:
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "31415",
        },
    )
    assert (response.status_code, response.text) == (200, "31415")


def test_a_signed_message_is_persisted_classified_and_answered(
    app: FastAPI, client: TestClient, seeded: Database, provider: StubProvider
) -> None:
    """The whole point of A4 and A5, in one block: a request in, a conversation out.

    The audit action is the difference A5 made. This assertion used to read ``auto_routed`` — the
    message was filed against whatever the rules said and the guest heard nothing.
    """
    assert _post(client, _payload()) == 200
    _drain(app)

    with seeded.session() as session:
        message = session.query(Message).one()
        assert message.external_id == "wamid.A"
        assert message.tenant_id == uuid.UUID(TENANT_ID)  # resolved through channel_configs

        audit = session.query(AuditLogRow).one()
        assert audit.action == "receptionist_reply"
        assert audit.actor == "bot"
        assert audit.classification_snapshot["intent"] == "booking_enquiry"
        assert audit.message_id == message.id

        classification = session.query(Classification).one()
        assert classification.message_id == message.id
        assert classification.intent == "booking_enquiry"
        assert classification.model_used == "stub-model"
        assert classification.prompt_version == PROMPT_VERSION
        assert classification.latency_ms >= 0

        item = session.query(InboxItem).one()
        assert item.status == InboxStatus.AUTO_ROUTED.value
        assert item.band == ConfidenceBand.HIGH.value
        assert item.message_id == message.id
        assert item.classification_id == classification.id  # filed with its reasoning attached

        # The conversation half: what was said, and the job it opened.
        conversation = session.query(Conversation).one()
        assert conversation.channel_thread_id == "966500000000"
        assert conversation.last_turn_at is not None

        turns = session.query(Turn).order_by(Turn.direction).all()
        assert [t.direction for t in turns] == ["inbound", "outbound"]
        assert turns[0].body_text == "Any rooms free in June?"
        assert turns[1].body_text  # we said something back

        task = session.query(TaskRow).one()
        assert task.intent == "booking_enquiry"
        assert task.conversation_id == conversation.id

    assert [call.text for call in provider.calls] == ["Any rooms free in June?"]


def test_clinic_greeting_uses_the_real_factory_and_composition_path(
    seeded: Database,
) -> None:
    """The provider sees the clinic prompt and the receptionist greets instead of escalating."""
    requests: list[httpx.Request] = []

    def classify(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "record_classification",
                        "input": {
                            "intent": "greeting",
                            "summary_one_line": "Patient says hello",
                            "language": "en",
                            "confidence_overall": 0.99,
                            "confidence_intent": 0.99,
                            "confidence_person": 0.5,
                            "confidence_company": 0.1,
                        },
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )

    settings = Settings(
        _env_file=None,
        meta_app_secret=APP_SECRET,
        meta_webhook_verify_token=VERIFY_TOKEN,
        anthropic_api_key="sk-ant",
        tenant_vertical="clinics",
        tenant_greeting_opening="Welcome to the clinic.",
    )
    vocabulary = settings.vocabulary()
    classifier = build_classifier(
        settings,
        httpx.Client(transport=httpx.MockTransport(classify)),
        vocabulary=vocabulary,
    )
    app = assemble(settings, seeded, classifier, vocabulary=vocabulary)

    assert _post(TestClient(app), _payload("wamid.clinic", "hi")) == 200
    _drain(app)

    assert len(requests) == 1
    provider_payload = json.loads(requests[0].content)
    clinic_prompt = provider_payload["system"][0]["text"]
    assert "### greeting" in clinic_prompt
    assert "holiday-home short stays" not in clinic_prompt

    with seeded.session() as session:
        audit = session.query(AuditLogRow).one()
        assert audit.action == "receptionist_reply"
        outbound = session.query(Turn).filter(Turn.direction == "outbound").one()
        assert outbound.body_text == "Welcome to the clinic."


def test_an_emergency_message_is_answered_and_filed_without_a_model(
    app: FastAPI, client: TestClient, seeded: Database, provider: StubProvider
) -> None:
    """G3 through the real graph: a gas leak never reaches the classifier.

    The assembled pipeline is the production one here — the same resolver, repository, queue,
    orchestrator and conversation store — so this is the end-to-end statement that the check runs
    where the module docstring says it does, and not merely that the detector works.
    """
    assert _post(client, _payload(text="there is a smell of gas in the kitchen")) == 200
    _drain(app)

    with seeded.session() as session:
        audit = session.query(AuditLogRow).one()
        assert audit.action == "emergency"
        assert audit.classification_snapshot["trigger_id"] == "gas"

        item = session.query(InboxItem).one()
        assert item.status == InboxStatus.NEEDS_REVIEW.value
        assert item.band == ConfidenceBand.HIGH.value
        # No model ran, so there is no classification for the item to point at.
        assert item.classification_id is None
        assert session.query(Classification).count() == 0

        # The guest was answered, and both halves are on the transcript.
        turns = session.query(Turn).order_by(Turn.direction).all()
        assert [t.direction for t in turns] == ["inbound", "outbound"]
        assert "emergency" in (turns[1].body_text or "").lower()

        # An emergency is nobody's job to progress, so no task row was opened for it.
        assert session.query(TaskRow).count() == 0

    assert provider.calls == []


def test_a_second_message_continues_the_same_conversation(
    settings: Settings, seeded: Database, provider: StubProvider
) -> None:
    """A5, stated as plainly as it can be: the second message resumes the first one's job.

    Before this, ``ConversationRepository`` was never called outside its own tests and the
    orchestrator passed ``task=None`` on every message. Two messages produced two unrelated
    classifications and no memory of either. They now produce one conversation, one task, and a
    transcript with both halves of both turns in it.

    Each turn is delivered through its own application over the same database, which is both how
    the guest experiences it — they reply after being answered — and a stronger claim than one
    process would make: the continuity is in the rows, so it survives a restart between turns.
    """
    for external_id, text in (
        ("wamid.1", "Any rooms free in June?"),
        ("wamid.2", "for two people"),
    ):
        turn_app = assemble(settings, seeded, Classifier(provider, provider))
        assert _post(TestClient(turn_app), _payload(external_id, text)) == 200
        _drain(turn_app)

    with seeded.session() as session:
        assert session.query(Message).count() == 2
        assert session.query(Conversation).count() == 1  # same thread, same conversation
        assert session.query(TaskRow).count() == 1  # same intent, same job continued
        assert session.query(Turn).count() == 4  # two asked, two answered
        assert session.query(Classification).count() == 2  # one row per message, as it should be

        task = session.query(TaskRow).one()
        assert task.status in {"collecting", "handed_off", "completed"}


def test_the_webhook_does_not_wait_for_the_model(settings: Settings, seeded: Database) -> None:
    """§5: 200 comes after persist-and-enqueue, before classification.

    Asserted by observing that the row the classifier writes is not there yet when the response
    arrives — a slow model must not turn one guest's message into a retried delivery.
    """
    blocking = BlockingProvider()
    app = assemble(settings, seeded, Classifier(blocking, blocking))
    client = TestClient(app)

    try:
        assert _post(client, _payload("wamid.slow")) == 200
        with seeded.session() as session:
            assert session.query(Message).count() == 1  # persisted before the response
            assert session.query(InboxItem).count() == 0  # classification still in flight
    finally:
        blocking.release()
        _drain(app)


def test_an_unconfigured_endpoint_is_not_attributed_to_anyone(
    client: TestClient, seeded: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """An unresolvable endpoint is dropped loudly rather than landing in some other tenant's inbox.

    Loudly, but with a 200: Meta reads anything else as "redeliver" and eventually disables the
    subscription, and no redelivery can write the ``channel_configs`` row that is actually missing.
    The guarantee under test is the isolation one — nothing is written for an endpoint we cannot
    attribute — plus the warning that lets an operator fix it.
    """
    body = _payload().replace(ENDPOINT_ID.encode(), b"UNKNOWN")

    with caplog.at_level(logging.WARNING, logger="apps.api.ingestion.router"):
        assert _post(client, body) == 200

    with seeded.session() as session:
        assert session.query(Message).count() == 0
    assert "UNKNOWN" in caplog.text


def test_low_confidence_is_filed_for_review(settings: Settings, seeded: Database) -> None:
    """The band that decides routing is the threshold the environment configured (tenant_policy)."""
    provider = StubProvider(confidence=0.2)
    app = assemble(settings, seeded, Classifier(provider, provider))

    assert _post(TestClient(app), _payload()) == 200
    _drain(app)

    with seeded.session() as session:
        item = session.query(InboxItem).one()
        assert item.status == InboxStatus.NEEDS_REVIEW.value
        assert item.band == ConfidenceBand.LOW.value


def test_a_duplicate_delivery_is_classified_once(
    app: FastAPI, client: TestClient, seeded: Database, provider: StubProvider
) -> None:
    """Idempotency survives the real repository: the platform re-delivers, we do not re-spend."""
    assert _post(client, _payload()) == 200
    assert _post(client, _payload()) == 200
    _drain(app)

    with seeded.session() as session:
        assert session.query(Message).count() == 1
        assert session.query(InboxItem).count() == 1
    assert len(provider.calls) == 1


def test_application_shutdown_stops_the_worker_pool(app: FastAPI, client: TestClient) -> None:
    """A process that stops has to stop its threads, or the container never exits.

    Shutdown order is asserted by the fact that this passes at all: the pool drains before the
    engine it is writing through is disposed, so the message accepted below is classified rather
    than failing against a closed connection.
    """
    assert _post(client, _payload()) == 200

    with TestClient(app):  # entering and leaving runs the application's lifespan
        pass

    with pytest.raises(RuntimeError):  # the pool refuses new work once it is shut down
        app.state.queue.enqueue(TENANT_ID, "wamid.later")


def test_assemble_stays_on_the_in_process_queue_without_redis_url(app: FastAPI) -> None:
    """B5: unset ``REDIS_URL`` is the same in-process path this service has run since A4."""
    assert isinstance(app.state.queue, ThreadPoolClassificationQueue)


def test_assemble_switches_to_the_redis_queue_when_redis_url_is_set(
    seeded: Database, provider: StubProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B5: the API becomes a thin producer — no sender/orchestrator built in this process."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", VERIFY_TOKEN)
    # Port 1: nothing is listening, and nothing needs to be — building the pool touches no
    # network (build_redis_pool's docstring), so this never actually connects.
    monkeypatch.setenv("REDIS_URL", "redis://localhost:1/0")
    settings = Settings(_env_file=None)

    app = assemble(settings, seeded, Classifier(provider, provider))

    assert isinstance(app.state.queue, RedisClassificationQueue)
    assert app.state.sender is None

    with TestClient(app):  # runs the lifespan; must close cleanly with nothing ever connected
        pass


def test_create_application_reports_what_the_environment_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured deploy fails at startup, naming the variable, before a message arrives."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        create_application(Settings(_env_file=None))
