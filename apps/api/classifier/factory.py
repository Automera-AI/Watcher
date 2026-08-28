"""Build the tiered classifier from configuration (roadmap A1 + A3, the join between them).

``Classifier`` takes providers; providers take keys and endpoints; keys and endpoints live in
``Settings``. This module is the one place that knows all three, so the composition root (A4) can
ask for a working classifier without knowing that Anthropic and OpenAI exist, and a test can build
one against a mock transport by passing its own ``httpx.Client``.

**Provider is chosen by model ID, not by a separate variable.** ``CLASSIFIER_MODEL_ESCALATION``
already names the model; a ``CLASSIFIER_PROVIDER_ESCALATION`` alongside it would be a second fact
that can disagree with the first, and the disagreement would be discovered in production. A name
starting ``claude`` goes to Anthropic, the self-hosted model ID goes to the self-hosted server,
and everything else speaks the OpenAI dialect — which is also true of most gateways worth pointing
at.
"""

from __future__ import annotations

from typing import Any

import httpx
from packages.intents.schema import Vocabulary

from apps.api.channels import ConfigError
from apps.api.classifier.anthropic import DEFAULT_MAX_OUTPUT_TOKENS, AnthropicProvider
from apps.api.classifier.openai import OpenAIProvider
from apps.api.classifier.prompt import build_system_prompt
from apps.api.classifier.provider import LLMProvider
from apps.api.classifier.service import Classifier
from apps.api.core.config import Settings

#: Claude models that think unless told not to. Classification is a labelling call with a fixed
#: output shape — there is nothing for a reasoning pass to add that the prompt's tie-breaks do
#: not already state — so thinking here is latency and spend on every inbound message.
_THINKS_BY_DEFAULT = ("claude-sonnet-5", "claude-opus-5")

#: Claude models that think unconditionally and reject ``{"type": "disabled"}`` with a 400.
#: They can still run the classifier; they just need room for the thinking they will do anyway.
_CANNOT_DISABLE_THINKING = ("claude-fable-5", "claude-mythos-5")

#: Enough headroom for a thinking pass plus the tool call, for the models above.
_MAX_OUTPUT_TOKENS_WITH_THINKING = 8192


def _thinking_policy(model_id: str) -> tuple[dict[str, Any] | None, int]:
    """``(thinking parameter, max_tokens)`` for one model.

    Three families, because the API's default differs by family and getting it wrong is silent
    rather than loud. Sending ``disabled`` where it is rejected is a 400 you find immediately;
    *omitting* it on a model that thinks by default is a bill and a truncation you find in
    production. Neither is a judgement call about the model — it is the vendor's contract, so it
    lives here rather than in a per-deploy environment variable an operator has to get right.

    Deliberately not set: ``effort``. It is the other spend lever on the Claude 5 family, and
    turning it down is a plausible saving — but accuracy is the product metric here, and item
    2.7 is what measures accuracy. Guessing at it before the eval can measure the result is how
    the 88% number stopped meaning anything the first time.
    """
    if model_id.startswith(_CANNOT_DISABLE_THINKING):
        return None, _MAX_OUTPUT_TOKENS_WITH_THINKING
    if model_id.startswith(_THINKS_BY_DEFAULT):
        return {"type": "disabled"}, DEFAULT_MAX_OUTPUT_TOKENS
    # Haiku 4.5, Sonnet 4.6, Opus 4.x: no thinking unless asked. Omitting is both correct and
    # the safest thing to send, since the older models predate the `disabled` value.
    return None, DEFAULT_MAX_OUTPUT_TOKENS


def build_provider(
    settings: Settings,
    model_id: str,
    client: httpx.Client | None = None,
    *,
    system_prompt: str | None = None,
) -> LLMProvider:
    """Concrete provider for one pinned model. Raises ``ConfigError`` if its key is missing."""
    credentials = settings.llm_credentials(model_id)
    http = client if client is not None else _default_client()
    prompt = (
        system_prompt if system_prompt is not None else build_system_prompt(settings.vocabulary())
    )

    if model_id.startswith("claude"):
        api_key = credentials.api_key
        if api_key is None:  # `llm_credentials` requires the key on this path; belt and braces.
            raise ConfigError("Missing required environment variable: ANTHROPIC_API_KEY")
        thinking, max_output_tokens = _thinking_policy(model_id)
        return AnthropicProvider(
            model_id,
            api_key,
            http,
            base_url=credentials.base_url,
            timeout_seconds=credentials.timeout_seconds,
            max_retries=credentials.max_retries,
            thinking=thinking,
            max_output_tokens=max_output_tokens,
            system_prompt=prompt,
        )

    return OpenAIProvider(
        model_id,
        credentials.api_key,
        http,
        base_url=credentials.base_url,
        timeout_seconds=credentials.timeout_seconds,
        max_retries=credentials.max_retries,
        system_prompt=prompt,
    )


def build_classifier(
    settings: Settings,
    client: httpx.Client | None = None,
    *,
    vocabulary: Vocabulary | None = None,
) -> Classifier:
    """The two-tier classifier described by ``CLASSIFIER_MODEL_*`` (D8-a).

    Both tiers share one ``httpx.Client`` so they share its connection pool: the escalation call
    follows the first-pass call to the same host within milliseconds, and re-establishing TLS for
    it would add more latency than the escalation itself costs.
    """
    http = client if client is not None else _default_client()
    prompt = build_system_prompt(vocabulary or settings.vocabulary())
    return Classifier(
        build_provider(settings, settings.classifier_model_first_pass, http, system_prompt=prompt),
        build_provider(settings, settings.classifier_model_escalation, http, system_prompt=prompt),
        escalation_threshold=settings.classifier_confidence_escalation_threshold,
    )


def _default_client() -> httpx.Client:
    """One pooled client. Per-request timeouts are passed by the providers, not set here."""
    return httpx.Client(limits=httpx.Limits(max_keepalive_connections=10, max_connections=20))
