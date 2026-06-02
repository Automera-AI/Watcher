"""Tests for destination recipes and reliable delivery (addendum §11)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from apps.api.destinations.delivery import (
    DeadLetterStore,
    WebhookDelivery,
    resolve_recipe_mapping,
)
from apps.api.destinations.recipes import RECIPES, UnknownFieldError, build_payload
from apps.api.schemas.classification import Classification


def _classification() -> Classification:
    return Classification(
        intent="new_lead",
        summary_one_line="Prospect asking about pricing",
        language="en",
        person_name="Sara",
        company_name="Acme",
        phone_e164="+966500000000",
        confidence_overall=0.95,
        confidence_intent=0.95,
        confidence_person=0.9,
        confidence_company=0.8,
        id=uuid4(),
        tenant_id=uuid4(),
        message_id=uuid4(),
        model_used="model-x",
        prompt_version="v1",
        latency_ms=10,
        created_at=datetime.now(UTC),
    )


def test_build_payload_maps_fields() -> None:
    payload = build_payload(_classification(), RECIPES["hubspot"].field_mapping)
    assert payload == {
        "firstname": "Sara",
        "company": "Acme",
        "phone": "+966500000000",
        "lead_note": "Prospect asking about pricing",
    }


def test_build_payload_rejects_unmappable_field() -> None:
    with pytest.raises(UnknownFieldError):
        build_payload(_classification(), {"x": "raw_payload"})


def test_resolve_recipe_mapping_applies_overrides() -> None:
    mapping = resolve_recipe_mapping(RECIPES["notion"], {"Name": "company_name"})
    assert mapping["Name"] == "company_name"  # overridden
    assert mapping["Phone"] == "phone_e164"  # default kept


class _OkTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, payload: dict[str, Any]) -> None:
        self.posts.append((url, payload))


class _FlakyTransport:
    """Fails ``fail_times`` then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self._remaining = fail_times
        self.calls = 0

    def post(self, url: str, payload: dict[str, Any]) -> None:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("boom")


class _RecordingDeadLetter:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any], str]] = []

    def record(self, destination_id: str, payload: dict[str, Any], error: str) -> None:
        self.records.append((destination_id, payload, error))


def test_delivery_succeeds_first_try() -> None:
    transport = _OkTransport()
    dead_letter: DeadLetterStore = _RecordingDeadLetter()
    result = WebhookDelivery(transport, dead_letter).deliver("d1", "https://x", {"a": 1})
    assert result.delivered is True
    assert result.attempts == 1
    assert transport.posts == [("https://x", {"a": 1})]


def test_delivery_retries_then_succeeds() -> None:
    transport = _FlakyTransport(fail_times=2)
    result = WebhookDelivery(transport, _RecordingDeadLetter(), max_attempts=3).deliver(
        "d1", "https://x", {"a": 1}
    )
    assert result.delivered is True
    assert result.attempts == 3


def test_delivery_dead_letters_on_exhaustion() -> None:
    transport = _FlakyTransport(fail_times=5)
    dead_letter = _RecordingDeadLetter()
    result = WebhookDelivery(transport, dead_letter, max_attempts=3).deliver(
        "d1", "https://x", {"a": 1}
    )
    assert result.delivered is False
    assert result.attempts == 3
    assert len(dead_letter.records) == 1
    assert dead_letter.records[0][0] == "d1"
