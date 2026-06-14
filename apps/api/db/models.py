"""ORM models — the full §4 data model.

Every business table carries ``tenant_id`` (RLS-enforced in Postgres, §3). Pydantic schemas remain
the single source of truth for LLM output / REST; these rows are their persistence. ``eval_runs`` is
the one table that is not tenant-scoped (it tracks model/prompt accuracy globally).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base, TimestampedTenantBase, _utcnow


class Tenant(Base):
    """A customer account; one auth-provider org maps to one tenant (addendum §2, §3)."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    tier: Mapped[str] = mapped_column(String(32))  # TenantTier value
    control_chat_phone_e164: Mapped[str | None] = mapped_column(String(20), default=None)
    waba_id: Mapped[str | None] = mapped_column(String(64), default=None)
    phone_number_id: Mapped[str | None] = mapped_column(String(64), default=None)


class Source(TimestampedTenantBase):
    """A watched WhatsApp conversation; opt-out model via ``excluded`` (addendum §4)."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("tenant_id", "wa_chat_id", name="uq_sources_tenant_chat"),)

    wa_chat_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # SourceKind value
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    excluded: Mapped[bool] = mapped_column(default=False)


class Message(TimestampedTenantBase):
    """A raw inbound/outbound message; ``wa_message_id`` is unique per tenant (idempotency, §5)."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "wa_message_id", name="uq_messages_tenant_wamid"),
    )

    wa_message_id: Mapped[str] = mapped_column(String(128))
    wa_chat_id: Mapped[str] = mapped_column(String(64), index=True)
    sender_phone_e164: Mapped[str] = mapped_column(String(20))
    sender_wa_name: Mapped[str | None] = mapped_column(String(255), default=None)
    direction: Mapped[str] = mapped_column(String(16))  # MessageDirection value
    type: Mapped[str] = mapped_column(String(16))  # MessageType value
    body_text: Mapped[str | None] = mapped_column(Text, default=None)
    media_id: Mapped[str | None] = mapped_column(String(128), default=None)
    media_mime: Mapped[str | None] = mapped_column(String(128), default=None)
    transcript_text: Mapped[str | None] = mapped_column(Text, default=None)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Classification(TimestampedTenantBase):
    """The LLM result + telemetry for one message (addendum §4 ``classifications``)."""

    __tablename__ = "classifications"

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    intent: Mapped[str] = mapped_column(String(32))
    person_name: Mapped[str | None] = mapped_column(String(255), default=None)
    person_appears_to_be: Mapped[str | None] = mapped_column(String(64), default=None)
    company_name: Mapped[str | None] = mapped_column(String(255), default=None)
    company_domain_hint: Mapped[str | None] = mapped_column(String(255), default=None)
    phone_e164: Mapped[str | None] = mapped_column(String(20), default=None)
    language: Mapped[str] = mapped_column(String(8))
    summary_one_line: Mapped[str] = mapped_column(Text)
    suggested_record_type: Mapped[str | None] = mapped_column(String(32), default=None)
    confidence_overall: Mapped[float] = mapped_column()
    confidence_intent: Mapped[float] = mapped_column()
    confidence_person: Mapped[float] = mapped_column()
    confidence_company: Mapped[float] = mapped_column()
    model_used: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column()


class InboxItem(TimestampedTenantBase):
    """A triage-queue item; auto-routed ones still appear marked ``auto`` (addendum §4, §12)."""

    __tablename__ = "inbox_items"

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    classification_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    status: Mapped[str] = mapped_column(String(16))  # InboxStatus value
    band: Mapped[str] = mapped_column(String(8))  # ConfidenceBand value
    assigned_action: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    resolved_by: Mapped[str | None] = mapped_column(String(64), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CrmCacheRow(TimestampedTenantBase):
    """Cached destination records we dedup against (addendum §4 ``crm_cache``, §9)."""

    __tablename__ = "crm_cache"

    external_record_id: Mapped[str] = mapped_column(String(128))
    record_type: Mapped[str | None] = mapped_column(String(32), default=None)
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    company: Mapped[str | None] = mapped_column(String(255), default=None)
    phones: Mapped[list[str]] = mapped_column(JSON, default=list)  # E.164 array
    emails: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_destination_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class IdentityResolutionRow(TimestampedTenantBase):
    """A recorded identity decision; ``considered`` powers 'never ask twice' (addendum §4, §9)."""

    __tablename__ = "identity_resolutions"

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    candidate_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(String(16))  # IdentityDecision value
    decided_by: Mapped[str | None] = mapped_column(String(64), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    considered: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Destination(TimestampedTenantBase):
    """A configured output (Sheets or webhook) + field mapping (addendum §4 ``destinations``)."""

    __tablename__ = "destinations"

    kind: Mapped[str] = mapped_column(String(16))  # DestinationKind value
    label: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    field_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RuleRow(TimestampedTenantBase):
    """A stored auto-routing rule (addendum §4 ``rules``, §12)."""

    __tablename__ = "rules"

    name: Mapped[str] = mapped_column(String(255))
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    action: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(default=0)


class AuditLogRow(TimestampedTenantBase):
    """Append-only record of every routing action (addendum §4 ``audit_log``)."""

    __tablename__ = "audit_log"

    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, default=None)
    action: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))  # "bot" or a user id
    classification_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    destination_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    destination_record_id: Mapped[str | None] = mapped_column(String(128), default=None)
    destination_record_url: Mapped[str | None] = mapped_column(String(512), default=None)


class EvalRun(Base):
    """An eval-tool run's metrics; not tenant-scoped (addendum §4 ``eval_runs``, §12)."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    golden_set_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    overall_accuracy: Mapped[float] = mapped_column()
    per_field: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    calibration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    per_language: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confusion: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
