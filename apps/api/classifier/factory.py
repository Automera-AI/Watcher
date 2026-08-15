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

import httpx

from apps.api.channels import ConfigError
from apps.api.classifier.anthropic import AnthropicProvider
from apps.api.classifier.openai import OpenAIProvider
from apps.api.classifier.provider import LLMProvider
from apps.api.classifier.service import Classifier
from apps.api.core.config import Settings


def build_provider(
    settings: Settings, model_id: str, client: httpx.Client | None = None
) -> LLMProvider:
    """Concrete provider for one pinned model. Raises ``ConfigError`` if its key is missing."""
    credentials = settings.llm_credentials(model_id)
    http = client if client is not None else _default_client()

    if model_id.startswith("claude"):
        api_key = credentials.api_key
        if api_key is None:  # `llm_credentials` requires the key on this path; belt and braces.
            raise ConfigError("Missing required environment variable: ANTHROPIC_API_KEY")
        return AnthropicProvider(
            model_id,
            api_key,
            http,
            base_url=credentials.base_url,
            timeout_seconds=credentials.timeout_seconds,
            max_retries=credentials.max_retries,
        )

    return OpenAIProvider(
        model_id,
        credentials.api_key,
        http,
        base_url=credentials.base_url,
        timeout_seconds=credentials.timeout_seconds,
        max_retries=credentials.max_retries,
    )


def build_classifier(settings: Settings, client: httpx.Client | None = None) -> Classifier:
    """The two-tier classifier described by ``CLASSIFIER_MODEL_*`` (D8-a).

    Both tiers share one ``httpx.Client`` so they share its connection pool: the escalation call
    follows the first-pass call to the same host within milliseconds, and re-establishing TLS for
    it would add more latency than the escalation itself costs.
    """
    http = client if client is not None else _default_client()
    return Classifier(
        build_provider(settings, settings.classifier_model_first_pass, http),
        build_provider(settings, settings.classifier_model_escalation, http),
        escalation_threshold=settings.classifier_confidence_escalation_threshold,
    )


def _default_client() -> httpx.Client:
    """One pooled client. Per-request timeouts are passed by the providers, not set here."""
    return httpx.Client(limits=httpx.Limits(max_keepalive_connections=10, max_connections=20))
