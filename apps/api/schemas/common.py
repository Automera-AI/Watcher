"""Shared field types and confidence-band logic used across the schemas."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from apps.api.schemas.enums import ConfidenceBand

# E.164 phone number, e.g. "+9665XXXXXXXX". Primary identity signal (addendum §9).
PhoneE164 = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{1,14}$")]

# A model-reported probability in [0, 1].
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

# Band thresholds keyed to the overall confidence.
#   HIGH  : ≥ 0.85 — auto-route. Matches the §8 escalation cutoff (re-run below this on pass one).
#   MEDIUM: ≥ 0.60 — ping the control chat for Confirm/Change/Skip (§10).
#   LOW   : < 0.60 — straight to the inbox, no ping.
# The exact v1.2 §3 rubric values are not restated in the addendum; these are the documented
# defaults and are tunable per tenant. Keep band logic in one place so routing stays consistent.
HIGH_CONFIDENCE_THRESHOLD = 0.85
MEDIUM_CONFIDENCE_THRESHOLD = 0.60


def band_for(confidence_overall: float) -> ConfidenceBand:
    """Map an overall confidence to its routing band (v1.2 §3 rubric)."""
    if confidence_overall >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceBand.HIGH
    if confidence_overall >= MEDIUM_CONFIDENCE_THRESHOLD:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW
