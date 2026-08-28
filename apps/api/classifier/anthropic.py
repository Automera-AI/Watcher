"""Anthropic implementation of the ``LLMProvider`` seam (roadmap A3, addendum §8 / D8-a).

This is the first code in the repository that makes a network call to a model. It talks to the
Messages API over plain HTTP rather than through the vendor SDK: the call is one POST with one
JSON body, the seam it satisfies is four lines long, and an SDK would put a second retry policy,
a second timeout default and a second dependency tree behind an interface that already has its
own.

**Tool-call mode, not "please reply with JSON".** The tool's ``input_schema`` is
``CLASSIFICATION_TOOL_SCHEMA`` — generated from ``ClassificationResult``, so the schema the model
is constrained by and the schema the service validates against cannot drift apart — and
``tool_choice`` forces that tool, so the model cannot answer in prose. Anything that still comes
back unusable is handled by the service's retry-then-unclear policy (§8), which is why a response
without a tool block returns ``{}`` here instead of raising: an empty dict fails validation, and
failing validation is already a defined path that ends at a human. A ``ProviderError`` means the
provider could not be reached at all, which is a different thing and deserves a different answer.

**The system block is marked cacheable, and that is the point.** The selected vertical's injected
prompt is byte-identical on every call by construction (``prompt.py`` renders everything variable
into the user turn). Sent uncached it is the largest line item in the classifier's bill and most
of its latency; sent with ``cache_control`` it is charged once per cache lifetime and read back at
a fraction of the input rate afterwards. The saving is reported as
:attr:`AnthropicProvider.last_usage` so a regression in it is visible rather than merely expensive.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from apps.api.classifier.prompt import (
    CLASSIFICATION_TOOL_DESCRIPTION,
    CLASSIFICATION_TOOL_NAME,
    CLASSIFICATION_TOOL_SCHEMA,
    render_user_prompt,
)
from apps.api.classifier.transport import TokenUsage, post_json
from apps.api.classifier.types import ClassificationInput

logger = logging.getLogger(__name__)

#: Pinned per Anthropic's versioning header contract; not a model version.
ANTHROPIC_API_VERSION = "2023-06-01"

#: The structured output is a few hundred tokens at most. A ceiling keeps a degenerate response
#: (a model that loops) from becoming a bill rather than an error.
#:
#: ``max_tokens`` bounds thinking *and* the tool call together, so this is only generous when
#: thinking is off — which is why the factory raises it for the models that cannot turn thinking
#: off. Left at 1024 with thinking on, a model can spend the whole budget reasoning and get cut
#: off before it emits the tool call, which reads downstream as invalid output rather than as
#: the truncation it is.
DEFAULT_MAX_OUTPUT_TOKENS = 1024


class AnthropicProvider:
    """Classifies one message with one Claude model. Satisfies ``LLMProvider`` structurally."""

    def __init__(
        self,
        model_id: str,
        api_key: str,
        client: httpx.Client,
        *,
        base_url: str = "https://api.anthropic.com",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        system_prompt: str,
        thinking: Mapping[str, Any] | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.model_id = model_id
        self._api_key = api_key
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._system_prompt = system_prompt
        self._thinking = thinking
        self._max_output_tokens = max_output_tokens
        self.last_usage: TokenUsage | None = None
        """Tokens billed by the most recent call, or ``None`` before the first one."""

    def complete_json(self, value: ClassificationInput) -> dict[str, Any]:
        """Return the model's tool input, or ``{}`` when it returned no usable tool call."""
        body = post_json(
            self._client,
            f"{self._base_url}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            payload=self._payload(value),
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        self.last_usage = _usage_from(body)
        return _tool_input_from(body, self.model_id)

    def _payload(self, value: ClassificationInput) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self._max_output_tokens,
            # No `temperature`. The Claude 5 family rejects a non-default sampling parameter
            # with a 400, and temperature never actually guaranteed identical output twice on
            # the models that did accept it — so the determinism it looked like it bought was
            # never real, and keeping it would make re-pinning a tier a code change.
            "system": [
                {
                    "type": "text",
                    "text": self._system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": render_user_prompt(value)}],
            "tools": [
                {
                    "name": CLASSIFICATION_TOOL_NAME,
                    "description": CLASSIFICATION_TOOL_DESCRIPTION,
                    "input_schema": CLASSIFICATION_TOOL_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": CLASSIFICATION_TOOL_NAME},
        }
        if self._thinking is not None:
            payload["thinking"] = dict(self._thinking)
        return payload


def _tool_input_from(body: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Pull the forced tool call's input out of the content blocks."""
    for block in body.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                return tool_input
    logger.warning(
        "%s returned no tool_use block (stop_reason=%s); treating as invalid output",
        model_id,
        body.get("stop_reason"),
    )
    return {}


def _usage_from(body: dict[str, Any]) -> TokenUsage:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    return TokenUsage(
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cached_input_tokens=int(usage.get("cache_read_input_tokens", 0)),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0)),
    )
