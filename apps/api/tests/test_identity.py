"""Tests for identity resolution (addendum §9)."""

from __future__ import annotations

from apps.api.identity.models import CrmRecord
from apps.api.identity.resolver import IncomingContact, decide, resolve
from apps.api.schemas.enums import IdentityDecision


def test_exact_phone_match_merges() -> None:
    rec = CrmRecord(external_record_id="r1", name="Sara", phones=["+966500000000"])
    res = resolve(IncomingContact(phone_e164="+966500000000", name="Different"), [rec])
    assert res.decision is IdentityDecision.MERGE
    assert res.score == 1.0
    assert res.candidate is rec


def test_strong_fuzzy_match_merges() -> None:
    rec = CrmRecord(external_record_id="r1", name="Acme Trading", company="Acme")
    res = resolve(IncomingContact(name="Acme Trading", company="Acme"), [rec])
    assert res.decision is IdentityDecision.MERGE


def test_no_candidates_is_new() -> None:
    res = resolve(IncomingContact(phone_e164="+966500000000", name="Sara"), [])
    assert res.decision is IdentityDecision.NEW
    assert res.candidate is None


# Band mapping is tested directly with explicit scores (fuzzy values are library-dependent).
def test_decide_band_boundaries() -> None:
    rec = CrmRecord(external_record_id="r1", name="Sara")
    assert decide(0.92, rec).decision is IdentityDecision.MERGE
    assert decide(0.91, rec).decision is IdentityDecision.LINK_RELATED
    assert decide(0.75, rec).decision is IdentityDecision.LINK_RELATED
    assert decide(0.74, rec).decision is IdentityDecision.NEW


def test_decide_without_candidate_is_new() -> None:
    assert decide(0.99, None).decision is IdentityDecision.NEW
