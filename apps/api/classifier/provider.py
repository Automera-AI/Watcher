"""LLM provider abstraction (addendum §8).

One internal interface with Anthropic / OpenAI / vLLM-Qwen implementations behind it — this enables
the self-hosted swap and cross-provider evals. A provider is bound to one model id and returns the
raw structured-output object; validation and the tiering policy live in the classifier service, so
every provider behaves identically from the service's point of view.

Concrete providers (``anthropic.py``, ``openai.py``, ``qwen.py``) land once the model IDs and keys
are pinned (§17 D8-a); they call their API in tool-call / constrained-decoding mode with
``CLASSIFICATION_TOOL_SCHEMA`` and return the resulting JSON object.
"""

from __future__ import annotations

from typing import Any, Protocol

from apps.api.classifier.types import ClassificationInput


class ProviderError(RuntimeError):
    """Transport/availability failure from a provider (the worker decides whether to retry)."""


class LLMProvider(Protocol):
    """Classifies one input into the structured-output JSON object for ``ClassificationResult``."""

    model_id: str

    def complete_json(self, value: ClassificationInput) -> dict[str, Any]:
        """Return the model's structured output as JSON. May raise :class:`ProviderError`."""
        ...
