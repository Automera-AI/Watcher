"""Per-tenant policy: the tunable knobs, in one place (DECISIONS.md customizability fix).

The review flagged that several values the spec calls "tunable per tenant" were global constants.
This bundles them into one object a tenant config can override, and converges the band thresholds
with the classifier's escalation cutoff so routing stays consistent. Defaults match DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.core.emergency import DEFAULT_TIMEZONE
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

    urgent_contact: str | None = None
    """A number this tenant's customers may ring directly in an emergency, if it has given one.

    Unset by default and never a literal in shared code — it is a different number for every
    tenant, and for a clinic it is a specific clinician rather than a support queue. Where it is
    set, ``emergency_reply`` puts it in the immediate reply so someone in trouble has it in the
    first message instead of waiting for the call back — added to the default wording, or
    substituted into ``emergency_reply`` below where the tenant supplies its own.
    """

    emergency_reply: str | None = None
    """This tenant's own emergency wording, replacing the default. May contain ``{contact}``.

    A clinic and a short-let operator need opposite things here. Telling a guest with a gas leak
    to call the fire service is correct; telling a patient to call an ambulance is a triage
    judgement, which is exactly what a clinic receptionist may not make — its own clinician
    decides that. Neither can be the shared default, so it is per tenant.
    """

    timezone: str = DEFAULT_TIMEZONE
    """Where this tenant's properties are, as an IANA zone name (roadmap G3).

    One emergency trigger is time-of-day dependent — locked out at 2pm is a support request, at
    2am it is a person on a street — and "night" is a fact about the guest's local clock, not the
    server's. A tenant in Cairo and a tenant in Dubai are an hour apart, which is an hour of the
    window either side of midnight, so this is per tenant rather than per process even though the
    control page is what will eventually set it.
    """

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
