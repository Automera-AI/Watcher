"""Shared field types and confidence-band logic used across the schemas."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from apps.api.schemas.enums import ConfidenceBand

# E.164 phone number, e.g. "+9665XXXXXXXX". Primary identity signal (addendum §9).
PhoneE164 = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{1,14}$")]

# A model-reported probability in [0, 1].
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

# Default band thresholds keyed to the overall confidence (DECISIONS.md):
#   HIGH  : ≥ 0.85 — auto-route. Matches the §8 escalation cutoff (re-run below this on pass one).
#   MEDIUM: ≥ 0.50 — ping the control chat for Confirm/Change/Skip (§10).
#   LOW   : < 0.50 — straight to the inbox, no ping.
# These are the per-tenant-tunable defaults; TenantPolicy (core/policy.py) can override them.
HIGH_CONFIDENCE_THRESHOLD = 0.85
MEDIUM_CONFIDENCE_THRESHOLD = 0.50


def band_for(
    confidence_overall: float,
    *,
    high_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
    medium_threshold: float = MEDIUM_CONFIDENCE_THRESHOLD,
) -> ConfidenceBand:
    """Map an overall confidence to its routing band (thresholds tunable per tenant)."""
    if confidence_overall >= high_threshold:
        return ConfidenceBand.HIGH
    if confidence_overall >= medium_threshold:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW
