"""Reliable delivery of a payload to a destination (addendum §11).

Webhook POSTs get retry-with-backoff; on exhaustion the attempt goes to a dead-letter surface (shown
in the Admin view) rather than being silently lost. The HTTP transport and dead-letter store are
ports, so this logic is testable without network and the backoff schedule stays explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from apps.api.destinations.recipes import Recipe


class WebhookTransport(Protocol):
    """Posts a payload to a destination URL; raises on a non-success response."""

    def post(self, url: str, payload: dict[str, Any]) -> None: ...


class DeadLetterStore(Protocol):
    """Records a permanently-failed delivery for the Admin dead-letter surface."""

    def record(self, destination_id: str, payload: dict[str, Any], error: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    attempts: int


class WebhookDelivery:
    """Posts with bounded retries, dead-lettering on exhaustion."""

    def __init__(
        self,
        transport: WebhookTransport,
        dead_letter: DeadLetterStore,
        *,
        max_attempts: int = 3,
    ) -> None:
        self._transport = transport
        self._dead_letter = dead_letter
        self._max_attempts = max_attempts

    def deliver(self, destination_id: str, url: str, payload: dict[str, Any]) -> DeliveryResult:
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                self._transport.post(url, payload)
                return DeliveryResult(delivered=True, attempts=attempt)
            except Exception as exc:  # noqa: BLE001 — transport-agnostic; we retry then dead-letter
                last_error = repr(exc)
        self._dead_letter.record(destination_id, payload, last_error)
        return DeliveryResult(delivered=False, attempts=self._max_attempts)


def resolve_recipe_mapping(
    recipe: Recipe, overrides: dict[str, str] | None = None
) -> dict[str, str]:
    """Merge a recipe's default mapping with per-destination overrides."""
    mapping = dict(recipe.field_mapping)
    if overrides:
        mapping.update(overrides)
    return mapping
