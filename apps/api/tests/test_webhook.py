"""End-to-end webhook route tests via FastAPI TestClient (addendum §5)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.app import create_app
from apps.api.channels.whatsapp import MetaSettings
from apps.api.ingestion.ports import UnknownEndpoint
from apps.api.ingestion.security import SIGNATURE_HEADER, expected_signature
from apps.api.tests.fakes import InMemoryRepository, RecordingQueue

SETTINGS = MetaSettings(app_secret="app-secret", webhook_verify_token="verify-token")


def _client() -> tuple[TestClient, InMemoryRepository, RecordingQueue]:
    repo = InMemoryRepository()
    queue = RecordingQueue()
    app = create_app(SETTINGS, repo, queue, resolve_tenant=lambda pid: pid or "default")
    return TestClient(app), repo, queue


def _text_payload() -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "PNID"},
                            "contacts": [{"profile": {"name": "Sara"}, "wa_id": "966500000000"}],
                            "messages": [
                                {
                                    "from": "966500000000",
                                    "id": "wamid.A",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "Need a quote"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def test_verify_handshake_echoes_challenge() -> None:
    client, _repo, _queue = _client()
    resp = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-token",
            "hub.challenge": "31415",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "31415"


def test_verify_handshake_rejects_wrong_token() -> None:
    client, _repo, _queue = _client()
    resp = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "31415"},
    )
    assert resp.status_code == 403


def test_post_with_valid_signature_ingests() -> None:
    client, repo, queue = _client()
    body = json.dumps(_text_payload()).encode()
    headers = {SIGNATURE_HEADER: expected_signature(SETTINGS.app_secret, body)}

    resp = client.post("/webhook", content=body, headers=headers)

    assert resp.status_code == 200
    assert [m.external_id for _t, m in repo.saved] == ["wamid.A"]
    assert queue.enqueued == [("PNID", "wamid.A")]  # tenant resolved from phone_number_id


def test_post_with_invalid_signature_is_rejected_and_ingests_nothing() -> None:
    client, repo, queue = _client()
    body = json.dumps(_text_payload()).encode()

    resp = client.post("/webhook", content=body, headers={SIGNATURE_HEADER: "sha256=bad"})

    assert resp.status_code == 403
    assert repo.saved == []
    assert queue.enqueued == []


def test_duplicate_delivery_still_returns_200_without_double_ingest() -> None:
    client, repo, queue = _client()
    body = json.dumps(_text_payload()).encode()
    headers = {SIGNATURE_HEADER: expected_signature(SETTINGS.app_secret, body)}

    first = client.post("/webhook", content=body, headers=headers)
    second = client.post("/webhook", content=body, headers=headers)

    assert first.status_code == second.status_code == 200
    assert len(repo.saved) == 1  # idempotent on wa_message_id (§5)
    assert len(queue.enqueued) == 1


def test_unconfigured_endpoint_is_acknowledged_not_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing ``channel_configs`` row must not become a 500.

    Meta reads a non-200 as "redeliver", and keeps reading it that way until it disables the
    subscription — which would take the callback down for every tenant sharing it over one number
    nobody configured. A redelivery cannot write the missing row, so there is nothing to retry.
    """
    repo = InMemoryRepository()
    queue = RecordingQueue()

    def reject(_endpoint: str | None) -> str:
        raise UnknownEndpoint("no enabled channel_configs row")

    client = TestClient(create_app(SETTINGS, repo, queue, resolve_tenant=reject))
    body = json.dumps(_text_payload()).encode()
    headers = {SIGNATURE_HEADER: expected_signature(SETTINGS.app_secret, body)}

    with caplog.at_level(logging.WARNING, logger="apps.api.ingestion.router"):
        resp = client.post("/webhook", content=body, headers=headers)

    assert resp.status_code == 200
    assert repo.saved == []  # nothing is attributed to a guessed tenant
    assert queue.enqueued == []


def test_unconfigured_endpoint_logs_the_id_and_the_dropped_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning is the recovery path, so it has to name the endpoint and keep the message.

    The id is otherwise unknowable — an unconfigured number has left no trace anywhere else — and
    the payload is the only copy of a guest's message once the delivery is acknowledged.
    """
    repo = InMemoryRepository()
    queue = RecordingQueue()

    def reject(_endpoint: str | None) -> str:
        raise UnknownEndpoint("no enabled channel_configs row")

    client = TestClient(create_app(SETTINGS, repo, queue, resolve_tenant=reject))
    body = json.dumps(_text_payload()).encode()
    headers = {SIGNATURE_HEADER: expected_signature(SETTINGS.app_secret, body)}

    with caplog.at_level(logging.WARNING, logger="apps.api.ingestion.router"):
        client.post("/webhook", content=body, headers=headers)

    assert len(caplog.records) == 1
    logged = caplog.records[0].getMessage()
    assert "PNID" in logged  # the id the operator has to configure
    assert "channel_configs" in logged  # and how to configure it
    assert "Need a quote" in logged  # the guest's message survives the drop
    assert "dropped 1 message" in logged


def test_one_unconfigured_endpoint_does_not_block_a_configured_one() -> None:
    """Meta batches changes; a batch is not all-or-nothing on one bad endpoint.

    Failing the whole request would strand the messages that *did* resolve behind a config gap on
    an unrelated number.
    """
    repo = InMemoryRepository()
    queue = RecordingQueue()

    def resolve_only_known(endpoint: str | None) -> str:
        if endpoint != "KNOWN":
            raise UnknownEndpoint(f"no enabled channel_configs row for {endpoint!r}")
        return "tenant-known"

    client = TestClient(create_app(SETTINGS, repo, queue, resolve_tenant=resolve_only_known))
    payload = _text_payload()
    known = json.loads(json.dumps(payload["entry"][0]["changes"][0]))
    known["value"]["metadata"]["phone_number_id"] = "KNOWN"
    known["value"]["messages"][0]["id"] = "wamid.KNOWN"
    payload["entry"][0]["changes"].append(known)

    body = json.dumps(payload).encode()
    headers = {SIGNATURE_HEADER: expected_signature(SETTINGS.app_secret, body)}

    resp = client.post("/webhook", content=body, headers=headers)

    assert resp.status_code == 200
    assert queue.enqueued == [("tenant-known", "wamid.KNOWN")]


def test_persist_failure_still_surfaces_as_a_retryable_500() -> None:
    """The transient case keeps its old behaviour — a redelivery genuinely can succeed."""
    queue = RecordingQueue()

    class FailingRepository(InMemoryRepository):
        def save(self, tenant_id: str, message: Any) -> None:
            raise RuntimeError("database is unreachable")

    client = TestClient(
        create_app(SETTINGS, FailingRepository(), queue, resolve_tenant=lambda pid: pid or "d"),
        raise_server_exceptions=False,
    )
    body = json.dumps(_text_payload()).encode()
    headers = {SIGNATURE_HEADER: expected_signature(SETTINGS.app_secret, body)}

    resp = client.post("/webhook", content=body, headers=headers)

    assert resp.status_code == 500
    assert queue.enqueued == []
