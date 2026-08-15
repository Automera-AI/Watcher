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


def _result_json(confidence: float, intent: str = "availability_check") -> dict[str, Any]:
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
    first = ScriptedProvider("cheap", [_result_json(0.50, intent="maintenance_issue")])
    escalation = ScriptedProvider("big", [_result_json(0.97, intent="availability_check")])
    outcome = Classifier(first, escalation).classify(_INPUT)

    assert outcome.escalated is True
    assert outcome.model_used == "big"
    assert outcome.result is not None and outcome.result.intent == "availability_check"
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
            external_id=f"wamid.{text}",
            thread_id="966500000000",
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


def test_the_outcome_carries_the_telemetry_the_classifications_table_needs() -> None:
    """A5's two columns, measured here because nowhere else can measure them.

    ``classifications`` sat empty for four sessions because ``latency_ms`` and ``prompt_version``
    had no honest source. The clock is injected so this asserts on a real measurement rather than
    on a test that sleeps.
    """
    ticks = iter([10.0, 10.25])
    outcome = Classifier(
        ScriptedProvider("cheap", [_result_json(0.95)]),
        ScriptedProvider("big", [_result_json(0.99)]),
        clock=lambda: next(ticks),
    ).classify(_INPUT)

    assert outcome.latency_ms == 250
    assert outcome.prompt_version == PROMPT_VERSION


def test_latency_spans_the_retries_and_the_escalation_it_paid_for() -> None:
    """A per-call number would report the fastest thing that happened and hide the slow path.

    This input is retried once on the cheap tier and then escalated — three model calls — and the
    guest waited for all of them.
    """
    ticks = iter([0.0, 1.5])
    outcome = Classifier(
        ScriptedProvider("cheap", [{"bad": 1}, _result_json(0.4)]),
        ScriptedProvider("big", [_result_json(0.99)]),
        clock=lambda: next(ticks),
    ).classify(_INPUT)

    assert outcome.escalated is True
    assert outcome.attempts == 3
    assert outcome.latency_ms == 1500


def test_an_unclear_outcome_still_reports_how_long_it_took() -> None:
    ticks = iter([0.0, 0.4])
    outcome = Classifier(
        ScriptedProvider("cheap", [{"bad": 1}, {"bad": 2}]),
        ScriptedProvider("big", [_result_json(0.99)]),
        clock=lambda: next(ticks),
    ).classify(_INPUT)

    assert outcome.is_unclear is True
    assert outcome.latency_ms == 400
