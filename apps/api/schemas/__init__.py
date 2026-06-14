"""Pydantic v2 schemas — single source of truth for LLM output, DB rows, and the REST contract.

Build-first per AGENTS.md and addendum §18.1. Import models from here rather than the submodules.
"""

from __future__ import annotations

from apps.api.schemas.classification import Classification, ClassificationResult
from apps.api.schemas.common import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    Confidence,
    PhoneE164,
    band_for,
)
from apps.api.schemas.enums import (
    ConfidenceBand,
    DestinationKind,
    IdentityDecision,
    InboxStatus,
    IntentType,
    Language,
    MessageDirection,
    MessageType,
    RecordType,
    SourceKind,
    TenantTier,
)
from apps.api.schemas.message import MessageEnvelope

__all__ = [
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "Classification",
    "ClassificationResult",
    "Confidence",
    "ConfidenceBand",
    "DestinationKind",
    "IdentityDecision",
    "InboxStatus",
    "IntentType",
    "Language",
    "MessageDirection",
    "MessageEnvelope",
    "MessageType",
    "PhoneE164",
    "RecordType",
    "SourceKind",
    "TenantTier",
    "band_for",
]
