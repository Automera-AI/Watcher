"""Identity-resolution value objects (addendum §9)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.enums import IdentityDecision


class CrmRecord(BaseModel):
    """A cached destination record we dedup against (addendum §4 ``crm_cache``)."""

    model_config = ConfigDict(extra="forbid")
    external_record_id: str
    name: str | None = None
    company: str | None = None
    phones: list[str] = []  # E.164


class IdentityCandidate(BaseModel):
    """A candidate match with its similarity score in [0, 1]."""

    record: CrmRecord
    score: float


class Resolution(BaseModel):
    """The outcome of resolving an incoming contact against the cache."""

    decision: IdentityDecision
    score: float
    candidate: CrmRecord | None = None
