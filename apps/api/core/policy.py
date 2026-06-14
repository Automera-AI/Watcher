"""Per-tenant policy: the tunable knobs, in one place (DECISIONS.md customizability fix).

The review flagged that several values the spec calls "tunable per tenant" were global constants.
This bundles them into one object a tenant config can override, and converges the band thresholds
with the classifier's escalation cutoff so routing stays consistent. Defaults match DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.identity.models import CrmRecord, Resolution
from apps.api.identity.resolver import MERGE_THRESHOLD, REVIEW_THRESHOLD, decide
from apps.api.schemas.common import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    band_for,
)
from apps.api.schemas.enums import ConfidenceBand


@dataclass(frozen=True, slots=True)
class TenantPolicy:
    """Tunable routing/identity/timing knobs for one tenant. Defaults per DECISIONS.md."""

    high_confidence_threshold: float = HIGH_CONFIDENCE_THRESHOLD
    medium_confidence_threshold: float = MEDIUM_CONFIDENCE_THRESHOLD
    identity_merge_threshold: float = MERGE_THRESHOLD
    identity_review_threshold: float = REVIEW_THRESHOLD
    control_token_ttl_seconds: int = 15 * 60
    classifier_max_attempts: int = 2
    delivery_max_attempts: int = 3

    def band(self, confidence_overall: float) -> ConfidenceBand:
        """Routing band under this tenant's thresholds."""
        return band_for(
            confidence_overall,
            high_threshold=self.high_confidence_threshold,
            medium_threshold=self.medium_confidence_threshold,
        )

    def identity_decision(self, score: float, candidate: CrmRecord | None) -> Resolution:
        """Identity decision under this tenant's match thresholds."""
        return decide(
            score,
            candidate,
            merge_threshold=self.identity_merge_threshold,
            review_threshold=self.identity_review_threshold,
        )


DEFAULT_POLICY = TenantPolicy()
