"""Tests for the per-tenant policy layer (DECISIONS.md customizability fix)."""

from __future__ import annotations

from apps.api.core.policy import DEFAULT_POLICY, TenantPolicy
from apps.api.identity.models import CrmRecord
from apps.api.schemas.enums import ConfidenceBand, IdentityDecision


def test_default_policy_matches_locked_thresholds() -> None:
    assert DEFAULT_POLICY.high_confidence_threshold == 0.85
    assert DEFAULT_POLICY.medium_confidence_threshold == 0.50
    assert DEFAULT_POLICY.band(0.90) is ConfidenceBand.HIGH
    assert DEFAULT_POLICY.band(0.50) is ConfidenceBand.MEDIUM
    assert DEFAULT_POLICY.band(0.49) is ConfidenceBand.LOW


def test_tenant_can_override_band_thresholds() -> None:
    strict = TenantPolicy(high_confidence_threshold=0.95, medium_confidence_threshold=0.7)
    # 0.90 is HIGH under defaults but only MEDIUM under the stricter tenant policy.
    assert DEFAULT_POLICY.band(0.90) is ConfidenceBand.HIGH
    assert strict.band(0.90) is ConfidenceBand.MEDIUM


def test_policy_identity_decision_uses_its_thresholds() -> None:
    rec = CrmRecord(external_record_id="r1", name="Sara")
    lenient = TenantPolicy(identity_merge_threshold=0.80)
    assert DEFAULT_POLICY.identity_decision(0.85, rec).decision is IdentityDecision.LINK_RELATED
    assert lenient.identity_decision(0.85, rec).decision is IdentityDecision.MERGE
