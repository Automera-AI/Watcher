"""Tests for the concrete Anthropic and OpenAI providers (roadmap A3).

Every test runs against an ``httpx.MockTransport``: no key, no network, no rate limit, and the
request the provider *would* have sent is available to assert on. That is the only way to test the
parts that matter before a key exists — that the system block is marked cacheable, that the tool
is forced, that a 429 is retried and a 401 is not, and that a model which answers in prose ends up
as an unclear message rather than an exception.

The cache assertions are load-bearing rather than decorative. Prompt caching is the difference
between the ~5k-token system prompt costing once per cache lifetime and costing once per inbound
message, and nothing else in the system would notice if it silently stopped working.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.api.classifier.anthropic import ANTHROPIC_API_VERSION, AnthropicProvider
from apps.api.classifier.factory import build_classifier, build_provider
from apps.api.classifier.openai import OpenAIProvider
from apps.api.classifier.prompt import (
    CLASSIFICATION_TOOL_NAME,
    SYSTEM_PROMPT,
    render_user_prompt,
)
from apps.api.classifier.provider import ProviderError
from apps.api.classifier.transport import post_json
from apps.api.classifier.types import ClassificationInput
from apps.api.core.config import Settings
from apps.api.schemas.enums import MessageType


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here may see a key, a model pin, or an endpoint from the machine it runs on."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


_INPUT = ClassificationInput(
    text="Is the 2-bed free from the 4th?",
    modality=MessageType.TEXT,
    sender_display_name="Layla",
    sender_phone="+971500000000",
)

_RESULT = {
    "intent": "availability_check",
    "summary_one_line": "Checking availability for a 2-bed from the 4th",
    "language": "en",
    "person_name": "Layla",
    "person_appears_to_be": "individual",
    "company_name": None,
    "company_domain_hint": None,
    "phone_e164": "+971500000000",
    "suggested_record_type": "individual_only",
    "confidence_overall": 0.93,
    "confidence_intent": 0.94,
    "confidence_person": 0.5,
    "confidence_company": 0.1,
}


class _Recorder:
    """Serves canned responses in order and keeps every request that was made."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def payload(self, index: int = 0) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[index].content)
        return body


def _anthropic_body(tool_input: dict[str, Any] | None = None, **usage: int) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if tool_input is not None:
        content.append({"type": "tool_use", "name": CLASSIFICATION_TOOL_NAME, "input": tool_input})
    return {
        "id": "msg_1",
        "content": content,
        "stop_reason": "tool_use" if tool_input is not None else "end_turn",
        "usage": {"input_tokens": 120, "output_tokens": 90, **usage},
    }


def _openai_body(arguments: str | None, **usage: Any) -> dict[str, Any]:
    tool_calls = (
        [
            {
                "type": "function",
                "function": {"name": CLASSIFICATION_TOOL_NAME, "arguments": arguments},
            }
        ]
        if arguments is not None
        else []
    )
    return {
        "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": tool_calls}}],
        "usage": {"prompt_tokens": 5200, "completion_tokens": 90, **usage},
    }


def _provider(recorder: _Recorder, **kwargs: Any) -> AnthropicProvider:
    return AnthropicProvider(
        "claude-haiku-4-5", "sk-ant", recorder.client(), max_retries=0, **kwargs
    )


# ── Anthropic ──────────────────────────────────────────────────────────────────────────────


def test_anthropic_returns_the_tool_input() -> None:
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))

    assert _provider(recorder).complete_json(_INPUT) == _RESULT


def test_anthropic_marks_the_system_block_cacheable() -> None:
    """The ~5k-token block is byte-identical per call and must be billed that way."""
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    _provider(recorder).complete_json(_INPUT)

    system = recorder.payload()["system"]
    assert system == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]


def test_anthropic_keeps_per_message_content_out_of_the_cached_block() -> None:
    """A cache hit needs an unchanged prefix, so nothing variable may reach the system block."""
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    provider = _provider(recorder)
    provider.complete_json(_INPUT)
    provider.complete_json(ClassificationInput(text="different", modality=MessageType.TEXT))

    first, second = recorder.payload(0), recorder.payload(1)
    assert first["system"] == second["system"]
    assert first["messages"] != second["messages"]
    assert "Is the 2-bed free from the 4th?" in first["messages"][0]["content"]


def test_anthropic_forces_the_classification_tool() -> None:
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    _provider(recorder).complete_json(_INPUT)

    payload = recorder.payload()
    assert payload["tool_choice"] == {"type": "tool", "name": CLASSIFICATION_TOOL_NAME}
    assert payload["tools"][0]["input_schema"]["type"] == "object"
    assert payload["model"] == "claude-haiku-4-5"
    assert payload["messages"][0]["content"] == render_user_prompt(_INPUT)

    headers = recorder.requests[0].headers
    assert headers["x-api-key"] == "sk-ant"
    assert headers["anthropic-version"] == ANTHROPIC_API_VERSION


def test_anthropic_never_sends_temperature() -> None:
    """The Claude 5 family rejects a non-default sampling parameter with a 400."""
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    _provider(recorder).complete_json(_INPUT)

    payload = recorder.payload()
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "top_k" not in payload


def test_anthropic_omits_thinking_unless_it_is_configured() -> None:
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    _provider(recorder).complete_json(_INPUT)

    assert "thinking" not in recorder.payload()


def test_anthropic_sends_the_configured_thinking_parameter() -> None:
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    _provider(recorder, thinking={"type": "disabled"}).complete_json(_INPUT)

    assert recorder.payload()["thinking"] == {"type": "disabled"}


def test_anthropic_records_cache_savings_as_usage() -> None:
    recorder = _Recorder(
        httpx.Response(
            200,
            json=_anthropic_body(
                _RESULT, cache_read_input_tokens=4800, cache_creation_input_tokens=0
            ),
        )
    )
    provider = _provider(recorder)
    provider.complete_json(_INPUT)

    assert provider.last_usage is not None
    assert provider.last_usage.cached_input_tokens == 4800
    assert provider.last_usage.cache_hit_ratio == pytest.approx(4800 / 4920)


def test_anthropic_response_without_a_tool_call_is_invalid_output_not_an_error() -> None:
    """A model answering in prose is the service's retry-then-unclear path, not an exception."""
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(None)))

    assert _provider(recorder).complete_json(_INPUT) == {}


# ── Retry policy (shared transport) ────────────────────────────────────────────────────────
#
# Exercised through `post_json` directly, because that is where `sleep` is injectable: a test of
# a backoff schedule should assert on the schedule, not wait out three seconds of it.


def _post(recorder: _Recorder, max_retries: int) -> dict[str, Any]:
    return post_json(
        recorder.client(),
        "https://api.example.test/v1/messages",
        headers={"content-type": "application/json"},
        payload={"model": "m"},
        timeout=30.0,
        max_retries=max_retries,
        sleep=recorder.sleep,
    )


def test_rate_limit_is_retried_then_succeeds() -> None:
    recorder = _Recorder(
        httpx.Response(429, headers={"retry-after": "1"}, text="slow down"),
        httpx.Response(200, json={"ok": True}),
    )

    assert _post(recorder, max_retries=2) == {"ok": True}
    assert len(recorder.requests) == 2
    assert recorder.slept == [1.0]  # honoured Retry-After rather than the default schedule


def test_server_errors_exhaust_retries_and_raise_provider_error() -> None:
    recorder = _Recorder(httpx.Response(503, text="upstream unavailable"))

    with pytest.raises(ProviderError, match="503"):
        _post(recorder, max_retries=2)

    assert len(recorder.requests) == 3  # the first attempt plus two retries
    assert recorder.slept == [0.5, 1.0]  # exponential, and no sleep after the last attempt


def test_authentication_failure_is_not_retried() -> None:
    recorder = _Recorder(httpx.Response(401, text="invalid x-api-key"))

    with pytest.raises(ProviderError, match="401"):
        _post(recorder, max_retries=2)

    assert len(recorder.requests) == 1
    assert recorder.slept == []


def test_absurd_retry_after_is_capped_rather_than_obeyed() -> None:
    """A guest is waiting on this call; ``Retry-After: 3600`` is a reason to fail, not to wait."""
    recorder = _Recorder(httpx.Response(429, headers={"retry-after": "3600"}, text="back off"))

    with pytest.raises(ProviderError):
        _post(recorder, max_retries=1)

    assert recorder.slept == [8.0]


def test_authentication_failure_surfaces_from_the_provider() -> None:
    recorder = _Recorder(httpx.Response(401, text="invalid x-api-key"))

    with pytest.raises(ProviderError, match="401"):
        _provider(recorder).complete_json(_INPUT)


def test_connection_failure_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider("claude-haiku-4-5", "sk-ant", client, max_retries=0)

    with pytest.raises(ProviderError, match="ConnectTimeout"):
        provider.complete_json(_INPUT)


# ── OpenAI ─────────────────────────────────────────────────────────────────────────────────


def test_openai_decodes_the_function_arguments() -> None:
    recorder = _Recorder(httpx.Response(200, json=_openai_body(json.dumps(_RESULT))))
    provider = OpenAIProvider("gpt-4o-mini", "sk-oai", recorder.client(), max_retries=0)

    assert provider.complete_json(_INPUT) == _RESULT

    payload = recorder.payload()
    assert payload["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert payload["tool_choice"]["function"]["name"] == CLASSIFICATION_TOOL_NAME
    assert recorder.requests[0].headers["authorization"] == "Bearer sk-oai"


def test_openai_unparseable_arguments_are_invalid_output_not_an_error() -> None:
    recorder = _Recorder(httpx.Response(200, json=_openai_body('{"intent": "availab')))
    provider = OpenAIProvider("gpt-4o-mini", "sk-oai", recorder.client(), max_retries=0)

    assert provider.complete_json(_INPUT) == {}


def test_openai_missing_tool_call_is_invalid_output() -> None:
    recorder = _Recorder(httpx.Response(200, json=_openai_body(None)))
    provider = OpenAIProvider("gpt-4o-mini", "sk-oai", recorder.client(), max_retries=0)

    assert provider.complete_json(_INPUT) == {}


def test_openai_usage_separates_cached_tokens_from_fresh_ones() -> None:
    """OpenAI counts cached tokens inside prompt_tokens; both providers must mean the same thing."""
    recorder = _Recorder(
        httpx.Response(
            200,
            json=_openai_body(json.dumps(_RESULT), prompt_tokens_details={"cached_tokens": 5000}),
        )
    )
    provider = OpenAIProvider("gpt-4o-mini", "sk-oai", recorder.client(), max_retries=0)
    provider.complete_json(_INPUT)

    assert provider.last_usage is not None
    assert provider.last_usage.cached_input_tokens == 5000
    assert provider.last_usage.input_tokens == 200  # 5200 prompt tokens, 5000 of them cached


def test_self_hosted_server_is_called_without_an_authorization_header() -> None:
    recorder = _Recorder(httpx.Response(200, json=_openai_body(json.dumps(_RESULT))))
    provider = OpenAIProvider(
        "qwen2.5-32b-instruct",
        None,
        recorder.client(),
        base_url="http://vllm.internal:8000/v1",
        max_retries=0,
    )
    provider.complete_json(_INPUT)

    assert "authorization" not in recorder.requests[0].headers
    assert str(recorder.requests[0].url) == "http://vllm.internal:8000/v1/chat/completions"


# ── Factory: configuration → wired classifier ──────────────────────────────────────────────


def _settings(**overrides: str) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_factory_routes_each_model_id_to_its_provider() -> None:
    settings = _settings(
        anthropic_api_key="sk-ant",
        openai_api_key="sk-oai",
        self_hosted_llm_model="qwen2.5-32b-instruct",
        self_hosted_llm_base_url="http://vllm.internal:8000/v1",
    )
    client = _Recorder().client()

    assert isinstance(build_provider(settings, "claude-sonnet-4-6", client), AnthropicProvider)
    assert isinstance(build_provider(settings, "gpt-4o-mini", client), OpenAIProvider)
    assert isinstance(build_provider(settings, "qwen2.5-32b-instruct", client), OpenAIProvider)


def test_factory_disables_thinking_on_the_models_that_think_by_default() -> None:
    """Sonnet 5 thinks unless told not to, and max_tokens bounds thinking + the tool call.

    Left alone, a classification can spend its whole budget reasoning and be cut off before it
    emits the tool call — which arrives downstream as invalid output, not as the truncation it
    is, and so gets retried and then filed as unclear. It is the expensive kind of silent.
    """
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    settings = _settings(anthropic_api_key="sk-ant")

    build_provider(settings, "claude-sonnet-5", recorder.client()).complete_json(_INPUT)

    payload = recorder.payload()
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 1024


def test_factory_omits_thinking_on_models_that_do_not_think_by_default() -> None:
    """Haiku 4.5 predates the `disabled` value; omitting is both correct and safe to send."""
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    settings = _settings(anthropic_api_key="sk-ant")

    build_provider(settings, "claude-haiku-4-5", recorder.client()).complete_json(_INPUT)

    assert "thinking" not in recorder.payload()


def test_factory_gives_headroom_to_models_that_cannot_disable_thinking() -> None:
    """Fable and Mythos reject `disabled`, so they get room for the thinking they will do."""
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    settings = _settings(anthropic_api_key="sk-ant")

    build_provider(settings, "claude-fable-5", recorder.client()).complete_json(_INPUT)

    payload = recorder.payload()
    assert "thinking" not in payload
    assert payload["max_tokens"] == 8192


def test_factory_builds_a_classifier_that_classifies() -> None:
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(_RESULT)))
    settings = _settings(anthropic_api_key="sk-ant")

    outcome = build_classifier(settings, recorder.client()).classify(_INPUT)

    assert outcome.result is not None
    assert outcome.result.intent == "availability_check"
    assert outcome.model_used == "claude-haiku-4-5"  # D8-a's pinned cheap tier
    assert outcome.escalated is False


def test_factory_escalation_threshold_comes_from_configuration() -> None:
    """A tenant that wants more second opinions changes a variable, not the code."""
    low_confidence = dict(_RESULT, confidence_overall=0.9)
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(low_confidence)))
    settings = _settings(
        anthropic_api_key="sk-ant", classifier_confidence_escalation_threshold="0.95"
    )

    outcome = build_classifier(settings, recorder.client()).classify(_INPUT)

    assert outcome.escalated is True
    assert outcome.model_used == "claude-sonnet-5"


def test_a_model_answering_in_prose_ends_as_unclear_not_as_a_crash() -> None:
    """The end-to-end shape of the §8 policy over a real provider: two invalid outputs → inbox."""
    recorder = _Recorder(httpx.Response(200, json=_anthropic_body(None)))
    settings = _settings(anthropic_api_key="sk-ant")

    outcome = build_classifier(settings, recorder.client()).classify(_INPUT)

    assert outcome.is_unclear
    assert len(recorder.requests) == 2  # retried once on the same tier, then gave up (§8)
