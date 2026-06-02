"""Classification schemas — the single source of truth shared by the LLM output, DB row, and REST.

Two layers, deliberately separated:

* ``ClassificationResult`` — exactly what the LLM must emit under constrained decoding
  (addendum §3, §8). ``extra="forbid"`` keeps the structured-output contract tight; a
  schema-invalid result is retried once then marked unclear → inbox (§8).
* ``Classification`` — the persisted/REST record: the result plus the telemetry our service
  annotates (model used, prompt version, latency) and the derived routing band
  (addendum §4 ``classifications``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import Confidence, PhoneE164, band_for
from apps.api.schemas.enums import ConfidenceBand, Language


class ClassificationResult(BaseModel):
    """The LLM's structured output for one message (mirrors the v1.2 §3 schema)."""

    model_config = ConfigDict(extra="forbid")

    # ``intent`` and ``suggested_record_type`` are open-vocabulary: the controlled list is
    # defined by v1.2 §3 and should be pinned to an enum once that taxonomy is confirmed.
    intent: str = Field(description="Why this message exists (v1.2 §3 controlled vocabulary).")
    summary_one_line: str = Field(description="One-line human-readable summary for the inbox row.")
    language: Language

    person_name: str | None = None
    person_appears_to_be: str | None = Field(
        default=None, description="Inferred role/relationship, e.g. 'prospective client'."
    )
    company_name: str | None = None
    company_domain_hint: str | None = None
    phone_e164: PhoneE164 | None = Field(
        default=None, description="Phone extracted from content, if any (E.164)."
    )
    suggested_record_type: str | None = Field(
        default=None, description="Destination record type, e.g. lead/contact (v1.2 §3 vocabulary)."
    )

    confidence_overall: Confidence
    confidence_intent: Confidence
    confidence_person: Confidence
    confidence_company: Confidence

    @property
    def band(self) -> ConfidenceBand:
        """Routing band derived from the overall confidence (§3 rubric / §10)."""
        return band_for(self.confidence_overall)


class Classification(ClassificationResult):
    """A persisted classification: LLM result + service telemetry + identity (addendum §4)."""

    # ``model_used`` is in pydantic's reserved ``model_`` namespace; keep the spec's field name.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: UUID
    tenant_id: UUID
    message_id: UUID
    model_used: str = Field(description="Exact model id that produced this result (§8).")
    prompt_version: str = Field(
        description="Prompt version; the eval tool keys regressions to it (§8, §13)."
    )
    latency_ms: int = Field(ge=0)
    created_at: datetime
