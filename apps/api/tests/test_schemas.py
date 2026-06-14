"""Tests for the core Pydantic schemas (addendum §3, §4)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.api.schemas import (
    Classification,
    ClassificationResult,
    ConfidenceBand,
    Language,
    MessageEnvelope,
    MessageType,
    SourceKind,
    band_for,
)


def _valid_result_kwargs() -> dict[str, object]:
    return {
        "intent": "new_lead",
        "summary_one_line": "Prospect asking about pricing",
        "language": Language.EN,
        "confidence_overall": 0.91,
        "confidence_intent": 0.93,
        "confidence_person": 0.80,
        "confidence_company": 0.70,
    }


@pytest.mark.parametrize(
    ("overall", "expected"),
    [
        (0.85, ConfidenceBand.HIGH),
        (0.999, ConfidenceBand.HIGH),
        (0.50, ConfidenceBand.MEDIUM),
        (0.84, ConfidenceBand.MEDIUM),
        (0.49, ConfidenceBand.LOW),
        (0.0, ConfidenceBand.LOW),
    ],
)
def test_band_for_thresholds(overall: float, expected: ConfidenceBand) -> None:
    assert band_for(overall) is expected


def test_classification_result_band_property() -> None:
    result = ClassificationResult(**_valid_result_kwargs())
    assert result.band is ConfidenceBand.HIGH


def test_classification_result_rejects_unknown_field() -> None:
    # extra="forbid" guards the constrained-output contract (§8).
    with pytest.raises(ValidationError):
        ClassificationResult(**_valid_result_kwargs(), surprise="x")  # type: ignore[call-arg]


def test_confidence_must_be_in_unit_interval() -> None:
    kwargs = _valid_result_kwargs()
    kwargs["confidence_overall"] = 1.5
    with pytest.raises(ValidationError):
        ClassificationResult(**kwargs)


def test_extracted_phone_must_be_e164() -> None:
    kwargs = _valid_result_kwargs()
    kwargs["phone_e164"] = "0501234567"  # missing +country code
    with pytest.raises(ValidationError):
        ClassificationResult(**kwargs)


def test_classification_keeps_model_used_field_name() -> None:
    # protected_namespaces=() lets us keep the spec field name without a pydantic warning.
    record = Classification(
        **_valid_result_kwargs(),
        id=uuid4(),
        tenant_id=uuid4(),
        message_id=uuid4(),
        model_used="some-model-id",
        prompt_version="v1",
        latency_ms=1234,
        created_at=datetime.now(UTC),
    )
    assert record.model_used == "some-model-id"
    assert record.band is ConfidenceBand.HIGH


def test_message_envelope_classifiable_text_prefers_body_then_transcript() -> None:
    base = {
        "wa_message_id": "wamid.1",
        "wa_chat_id": "chat.1",
        "source_kind": SourceKind.DIRECT,
        "sender_phone_e164": "+966500000000",
        "type": MessageType.TEXT,
        "received_at": datetime.now(UTC),
    }
    text_msg = MessageEnvelope(**base, body_text="hello")
    assert text_msg.classifiable_text == "hello"

    voice_msg = MessageEnvelope(
        **{**base, "type": MessageType.AUDIO},
        transcript_text="transcribed hello",
    )
    assert voice_msg.classifiable_text == "transcribed hello"


def test_message_envelope_rejects_bad_phone() -> None:
    with pytest.raises(ValidationError):
        MessageEnvelope(
            wa_message_id="wamid.2",
            wa_chat_id="chat.2",
            source_kind=SourceKind.DIRECT,
            sender_phone_e164="not-a-phone",
            type=MessageType.TEXT,
            received_at=datetime.now(UTC),
        )
