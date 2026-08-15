"""HTTP plumbing shared by the concrete LLM providers (roadmap A3).

The two providers differ only in the JSON they send and the JSON they read back. Everything
between — timeouts, which failures are worth retrying, how long to wait, what a 401 means — is
identical and lives here, so a change to the retry policy cannot apply to Anthropic and quietly
not to OpenAI.

**What counts as retryable.** A 429 or a 5xx is the provider telling us to come back; a dropped
connection or a timeout is the network saying the same thing less politely. Those are retried with
exponential backoff, honouring ``Retry-After`` when the provider sends one. A 400 or a 401 is a
statement about the request itself — a malformed schema, a revoked key — and retrying it three
times only delays the error and triples the log noise, so it raises immediately.

Everything that escapes this module is a :class:`~apps.api.classifier.provider.ProviderError`. The
caller (the orchestrator, eventually a queue with its own redelivery) decides what a failed
message is worth; it should not have to know that ``httpx`` exists to make that decision.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from apps.api.classifier.provider import ProviderError

logger = logging.getLogger(__name__)

#: Statuses worth a second attempt: rate limits, and the provider having a bad minute.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

#: First backoff step in seconds; doubles per attempt (0.5s, 1s, 2s …).
_BACKOFF_BASE_SECONDS = 0.5

#: Never sleep longer than this on a provider's say-so. A guest is waiting on this call, and a
#: ``Retry-After: 3600`` is a reason to fail the message, not to hold the worker for an hour.
_MAX_BACKOFF_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens billed for one call — the unit-economics number the roadmap says is unmeasured.

    ``cached_input_tokens`` is what prompt caching actually saved: the share of the ~5k-token
    system block that was served from the provider's cache instead of being charged at the full
    input rate. It is reported separately from ``input_tokens`` because the two are billed at
    different rates, and because a cache hit ratio that quietly falls to zero — someone edits the
    vocabulary on every request, say — is a cost regression that shows up nowhere else.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def cache_hit_ratio(self) -> float:
        """Share of input tokens served from cache, in [0, 1]. Zero when nothing was sent."""
        total = self.input_tokens + self.cached_input_tokens
        return self.cached_input_tokens / total if total else 0.0


def post_json(
    client: httpx.Client,
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: float,
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """POST ``payload``, returning the decoded JSON body or raising :class:`ProviderError`.

    ``sleep`` is injected so the retry policy can be tested without the test taking as long as
    the backoff it is asserting on.
    """
    last_error: str = "no attempt was made"

    for attempt in range(max_retries + 1):
        retry_after: str | None = None
        try:
            response = client.post(url, headers=dict(headers), json=dict(payload), timeout=timeout)
        except httpx.HTTPError as exc:  # timeouts, connection resets, DNS — all retryable
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400:
                try:
                    body: dict[str, Any] = response.json()
                except ValueError as exc:
                    raise ProviderError(f"provider returned a non-JSON body: {exc}") from exc
                return body

            if response.status_code not in RETRYABLE_STATUS:
                # A request-shaped problem: a malformed tool schema, a revoked key. Retrying
                # spends the timeout budget three times over and ends in the same place.
                raise ProviderError(f"HTTP {response.status_code}: {_body_excerpt(response)}")

            last_error = f"HTTP {response.status_code}: {_body_excerpt(response)}"
            retry_after = response.headers.get("retry-after")

        logger.warning(
            "llm request failed (attempt %d/%d): %s", attempt + 1, max_retries + 1, last_error
        )
        if attempt < max_retries:
            sleep(_backoff_seconds(attempt + 1, retry_after=retry_after))

    raise ProviderError(f"provider unreachable after {max_retries + 1} attempts — {last_error}")


def _backoff_seconds(attempt: int, *, retry_after: str | None) -> float:
    """Exponential backoff, overridden by a sane ``Retry-After`` when the provider sends one."""
    if retry_after is not None:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass  # HTTP-date form; fall through to our own schedule
    return min(_BACKOFF_BASE_SECONDS * 2.0 ** (attempt - 1), _MAX_BACKOFF_SECONDS)


def _body_excerpt(response: httpx.Response, limit: int = 300) -> str:
    """A short, log-safe slice of an error body — providers echo request content in errors."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - a body we cannot even read must not mask the status
        return "<unreadable body>"
    return text[:limit]
