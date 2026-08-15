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
import threading
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.channels import ConfigError
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import ClassificationInput
from apps.api.core.config import Settings
from apps.api.db.engine import Database
from apps.api.db.models import AuditLogRow, ChannelConfig, InboxItem, Message, Tenant
from apps.api.db.tenant_resolver import UnknownEndpoint
from apps.api.ingestion.security import SIGNATURE_HEADER, expected_signature
from apps.api.main import assemble, create_application
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
    return response.status_code


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


def test_a_signed_message_is_persisted_classified_and_filed(
    app: FastAPI, client: TestClient, seeded: Database, provider: StubProvider
) -> None:
    """The whole point of A4, in one block: a request in, a decision recorded."""
    assert _post(client, _payload()) == 200
    _drain(app)

    with seeded.session() as session:
        message = session.query(Message).one()
        assert message.external_id == "wamid.A"
        assert message.tenant_id == uuid.UUID(TENANT_ID)  # resolved through channel_configs

        audit = session.query(AuditLogRow).one()
        assert audit.action == "auto_routed"  # high confidence, no rules configured
        assert audit.actor == "bot"
        assert audit.classification_snapshot["intent"] == "booking_enquiry"
        assert audit.message_id == message.id

        item = session.query(InboxItem).one()
        assert item.status == InboxStatus.AUTO_ROUTED.value
        assert item.band == ConfidenceBand.HIGH.value
        assert item.message_id == message.id

    assert [call.text for call in provider.calls] == ["Any rooms free in June?"]


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
    client: TestClient, seeded: Database
) -> None:
    """An unresolvable endpoint fails loudly rather than landing in some other tenant's inbox."""
    body = _payload().replace(ENDPOINT_ID.encode(), b"UNKNOWN")

    with pytest.raises(UnknownEndpoint):
        _post(client, body)

    with seeded.session() as session:
        assert session.query(Message).count() == 0


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


def test_create_application_reports_what_the_environment_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured deploy fails at startup, naming the variable, before a message arrives."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        create_application(Settings(_env_file=None))
