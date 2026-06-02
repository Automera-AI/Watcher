"""Tests for the tiered classifier: validation/retry + escalation policy (addendum §8)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apps.api.classifier.prompt import (
    CLASSIFICATION_TOOL_SCHEMA,
    PROMPT_VERSION,
    render_user_prompt,
)
from apps.api.classifier.service import Classifier
from apps.api.classifier.types import ClassificationInput, input_from
from apps.api.schemas.enums import MessageType, SourceKind
from apps.api.schemas.message import MessageEnvelope


def _result_json(confidence: float, intent: str = "new_lead") -> dict[str, Any]:
    return {
        "intent": intent,
        "summary_one_line": "summary",
        "language": "en",
        "confidence_overall": confidence,
        "confidence_intent": confidence,
        "confidence_person": confidence,
        "confidence_company": confidence,
    }


class ScriptedProvider:
    """LLMProvider double: returns successive scripted JSON objects, counting calls."""

    def __init__(self, model_id: str, responses: list[dict[str, Any]]) -> None:
        self.model_id = model_id
        self._responses = responses
        self.calls = 0

    def complete_json(self, value: ClassificationInput) -> dict[str, Any]:
        response = self._responses[self.calls]
        self.calls += 1
        return response


_INPUT = ClassificationInput(text="Need a quote", modality=MessageType.TEXT)


def test_high_confidence_first_pass_does_not_escalate() -> None:
    first = ScriptedProvider("cheap", [_result_json(0.95)])
    escalation = ScriptedProvider("big", [_result_json(0.99)])
    outcome = Classifier(first, escalation).classify(_INPUT)

    assert outcome.result is not None
    assert outcome.model_used == "cheap"
    assert outcome.escalated is False
    assert escalation.calls == 0


def test_low_confidence_escalates_and_takes_larger_model_result() -> None:
    first = ScriptedProvider("cheap", [_result_json(0.50, intent="unsure")])
    escalation = ScriptedProvider("big", [_result_json(0.97, intent="new_lead")])
    outcome = Classifier(first, escalation).classify(_INPUT)

    assert outcome.escalated is True
    assert outcome.model_used == "big"
    assert outcome.result is not None and outcome.result.intent == "new_lead"
    assert escalation.calls == 1


def test_schema_invalid_then_valid_retries_once() -> None:
    first = ScriptedProvider("cheap", [{"bad": "shape"}, _result_json(0.95)])
    escalation = ScriptedProvider("big", [_result_json(0.99)])
    outcome = Classifier(first, escalation).classify(_INPUT)

    assert first.calls == 2
    assert outcome.result is not None
    assert outcome.attempts == 2


def test_two_invalid_outputs_mark_unclear() -> None:
    first = ScriptedProvider("cheap", [{"bad": 1}, {"bad": 2}])
    escalation = ScriptedProvider("big", [_result_json(0.99)])
    outcome = Classifier(first, escalation).classify(_INPUT)

    assert outcome.is_unclear
    assert outcome.result is None
    assert escalation.calls == 0  # never reached the confidence check


def test_failed_escalation_falls_back_to_first_pass_result() -> None:
    first = ScriptedProvider("cheap", [_result_json(0.40)])
    escalation = ScriptedProvider("big", [{"bad": 1}, {"bad": 2}])
    outcome = Classifier(first, escalation).classify(_INPUT)

    assert outcome.escalated is True
    assert outcome.model_used == "cheap"  # kept the usable first-pass result
    assert outcome.result is not None and outcome.result.confidence_overall == 0.40


def test_input_from_message_builds_history_oldest_first() -> None:
    def msg(text: str) -> MessageEnvelope:
        return MessageEnvelope(
            wa_message_id=f"wamid.{text}",
            wa_chat_id="966500000000",
            source_kind=SourceKind.DIRECT,
            sender_phone_e164="+966500000000",
            type=MessageType.TEXT,
            body_text=text,
            received_at=datetime.now(UTC),
        )

    value = input_from(msg("now"), history=[msg("earlier")])
    assert value.text == "now"
    assert [t.text for t in value.history] == ["earlier"]
    assert value.history[0].role == "contact"


def test_prompt_metadata_is_wired() -> None:
    assert PROMPT_VERSION
    assert CLASSIFICATION_TOOL_SCHEMA["type"] == "object"
    rendered = render_user_prompt(_INPUT)
    assert "Need a quote" in rendered
