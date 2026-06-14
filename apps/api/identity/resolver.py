"""Resolve an incoming contact against cached CRM records (addendum §9).

Signals, in order of strength:
1. **Exact phone** (E.164) — the primary signal; an exact hit is a confident merge.
2. **Fuzzy name+company** via rapidfuzz ``token_set_ratio``.

Thresholds (tunable per tenant): ≥0.92 → merge candidate, 0.75–0.92 → surface for review
(link_related), <0.75 → new. Dedup runs against our own cache, not the live CRM (§9).
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from apps.api.identity.models import CrmRecord, Resolution
from apps.api.schemas.enums import IdentityDecision

MERGE_THRESHOLD = 0.92
REVIEW_THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class IncomingContact:
    """The contact extracted from a message (classification + sender phone)."""

    phone_e164: str | None = None
    name: str | None = None
    company: str | None = None


def _fuzzy_score(incoming: IncomingContact, record: CrmRecord) -> float:
    incoming_key = f"{incoming.name or ''} {incoming.company or ''}".strip()
    record_key = f"{record.name or ''} {record.company or ''}".strip()
    if not incoming_key or not record_key:
        return 0.0
    return float(fuzz.token_set_ratio(incoming_key, record_key)) / 100.0


def decide(
    score: float,
    candidate: CrmRecord | None,
    *,
    merge_threshold: float = MERGE_THRESHOLD,
    review_threshold: float = REVIEW_THRESHOLD,
) -> Resolution:
    """Map a similarity score to a decision (≥merge → merge, ≥review → review, else new)."""
    if candidate is not None and score >= merge_threshold:
        return Resolution(decision=IdentityDecision.MERGE, score=score, candidate=candidate)
    if candidate is not None and score >= review_threshold:
        return Resolution(decision=IdentityDecision.LINK_RELATED, score=score, candidate=candidate)
    return Resolution(decision=IdentityDecision.NEW, score=score, candidate=None)


def resolve(incoming: IncomingContact, candidates: list[CrmRecord]) -> Resolution:
    """Pick the best candidate and decide merge / link_related / new."""
    if incoming.phone_e164 is not None:
        for record in candidates:
            if incoming.phone_e164 in record.phones:
                return Resolution(decision=IdentityDecision.MERGE, score=1.0, candidate=record)

    best: CrmRecord | None = None
    best_score = 0.0
    for record in candidates:
        score = _fuzzy_score(incoming, record)
        if score > best_score:
            best, best_score = record, score

    return decide(best_score, best)
