"""OpenAI implementation of the ``LLMProvider`` seam (roadmap A3, addendum §8 / D8-a).

Two jobs in one class. The first is D8-a's cross-provider peer fallback: a second opinion from a
different vendor, which is what makes a cross-provider eval possible and what keeps one provider's
outage from being ours. The second is the self-hosted tier — a vLLM server speaks the OpenAI
chat-completions dialect, so pointing this class at ``SELF_HOSTED_LLM_BASE_URL`` is the whole of
the Qwen path, and ``api_key`` is optional for exactly that reason: a model server reachable only
from inside the perimeter authenticates by network position, not by bearer token.

Differences from the Anthropic provider that are worth knowing:

* **Arguments arrive as a JSON string**, not a parsed object, so a syntactically broken tool call
  is possible here in a way it is not there. It is handled the same way as a missing tool call —
  return ``{}``, let the service's validate-retry-then-unclear policy (§8) deal with it.
* **Caching is automatic and prefix-based**: there is no ``cache_control`` to set, and the saving
  is earned by putting the static system block first and never varying it. ``prompt.py`` already
  guarantees that, and ``usage.prompt_tokens_details.cached_tokens`` reports what it was worth.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from apps.api.classifier.prompt import (
    CLASSIFICATION_TOOL_DESCRIPTION,
    CLASSIFICATION_TOOL_NAME,
    CLASSIFICATION_TOOL_SCHEMA,
    SYSTEM_PROMPT,
    render_user_prompt,
)
from apps.api.classifier.transport import TokenUsage, post_json
from apps.api.classifier.types import ClassificationInput

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS = 1024


class OpenAIProvider:
    """Classifies one message with one OpenAI-dialect model (OpenAI proper, or self-hosted)."""

    def __init__(
        self,
        model_id: str,
        api_key: str | None,
        client: httpx.Client,
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.model_id = model_id
        self._api_key = api_key
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._system_prompt = system_prompt
        self.last_usage: TokenUsage | None = None
        """Tokens billed by the most recent call, or ``None`` before the first one."""

    def complete_json(self, value: ClassificationInput) -> dict[str, Any]:
        """Return the model's tool arguments, or ``{}`` when it returned nothing usable."""
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        body = post_json(
            self._client,
            f"{self._base_url}/chat/completions",
            headers=headers,
            payload=self._payload(value),
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        self.last_usage = _usage_from(body)
        return _tool_arguments_from(body, self.model_id)

    def _payload(self, value: ClassificationInput) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "temperature": 0,
            # The static block first, every time: prefix caching is what makes the 5k-token
            # system prompt affordable, and it only applies to an unchanged leading prefix.
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": render_user_prompt(value)},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": CLASSIFICATION_TOOL_NAME,
                        "description": CLASSIFICATION_TOOL_DESCRIPTION,
                        "parameters": CLASSIFICATION_TOOL_SCHEMA,
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": CLASSIFICATION_TOOL_NAME},
            },
        }


def _tool_arguments_from(body: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Decode the forced function call's ``arguments`` JSON string."""
    choices = body.get("choices")
    message = choices[0].get("message", {}) if isinstance(choices, list) and choices else {}
    for call in message.get("tool_calls") or []:
        arguments = call.get("function", {}).get("arguments")
        if not isinstance(arguments, str):
            continue
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError as exc:
            logger.warning("%s returned unparseable tool arguments: %s", model_id, exc)
            return {}
        if isinstance(decoded, dict):
            return decoded

    logger.warning(
        "%s returned no tool call (finish_reason=%s); treating as invalid output",
        model_id,
        choices[0].get("finish_reason") if isinstance(choices, list) and choices else None,
    )
    return {}


def _usage_from(body: dict[str, Any]) -> TokenUsage:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    details = usage.get("prompt_tokens_details")
    cached = int(details.get("cached_tokens", 0)) if isinstance(details, dict) else 0
    # OpenAI counts cached tokens *inside* prompt_tokens; the Anthropic shape reports them
    # alongside. Subtract so `TokenUsage.input_tokens` means the same thing for both providers.
    return TokenUsage(
        input_tokens=max(int(usage.get("prompt_tokens", 0)) - cached, 0),
        output_tokens=int(usage.get("completion_tokens", 0)),
        cached_input_tokens=cached,
    )
