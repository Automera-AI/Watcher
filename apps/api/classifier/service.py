"""Tiered classifier: cheap first pass, escalate the uncertain ones (addendum §8).

Policy:
* First pass on the cheap-tier provider.
* If the model returns schema-invalid JSON, retry once on the same tier; a second failure marks the
  message unclear → inbox.
* If the (valid) first-pass ``confidence_overall`` is below the escalation threshold, re-run the
  same input through the larger model and take its result. If escalation itself fails validation
  twice, keep the usable first-pass result.
"""

from __future__ import annotations

from pydantic import ValidationError

from apps.api.classifier.provider import LLMProvider
from apps.api.classifier.types import ClassificationInput, ClassificationOutcome
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.common import HIGH_CONFIDENCE_THRESHOLD

_MAX_ATTEMPTS_PER_TIER = 2


class Classifier:
    """Runs the two-tier classification policy over injected providers."""

    def __init__(
        self,
        first_pass: LLMProvider,
        escalation: LLMProvider,
        *,
        escalation_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._first_pass = first_pass
        self._escalation = escalation
        self._threshold = escalation_threshold

    def classify(self, value: ClassificationInput) -> ClassificationOutcome:
        result, attempts = self._attempt(self._first_pass, value)
        if result is None:
            return ClassificationOutcome(
                None, self._first_pass.model_id, escalated=False, attempts=attempts
            )

        if result.confidence_overall >= self._threshold:
            return ClassificationOutcome(
                result, self._first_pass.model_id, escalated=False, attempts=attempts
            )

        escalated_result, escalation_attempts = self._attempt(self._escalation, value)
        attempts += escalation_attempts
        if escalated_result is not None:
            return ClassificationOutcome(
                escalated_result, self._escalation.model_id, escalated=True, attempts=attempts
            )
        # Escalation produced no valid result; keep the usable first-pass one.
        return ClassificationOutcome(
            result, self._first_pass.model_id, escalated=True, attempts=attempts
        )

    def _attempt(
        self, provider: LLMProvider, value: ClassificationInput
    ) -> tuple[ClassificationResult | None, int]:
        """Call a provider up to twice, validating the structured output (§8)."""
        for attempt in range(1, _MAX_ATTEMPTS_PER_TIER + 1):
            raw = provider.complete_json(value)
            try:
                return ClassificationResult.model_validate(raw), attempt
            except ValidationError:
                continue
        return None, _MAX_ATTEMPTS_PER_TIER
