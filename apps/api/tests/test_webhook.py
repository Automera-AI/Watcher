"""End-to-end webhook route tests via FastAPI TestClient (addendum §5)."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from apps.api.app import create_app
from apps.api.core.config import MetaSettings
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
    assert [m.wa_message_id for _t, m in repo.saved] == ["wamid.A"]
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
