"""ORM models — the full §4 data model.

Every business table carries ``tenant_id`` (RLS-enforced in Postgres, §3). Pydantic schemas remain
the single source of truth for LLM output / REST; these rows are their persistence. ``eval_runs`` is
the one table that is not tenant-scoped (it tracks model/prompt accuracy globally).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base, TimestampedTenantBase, _utcnow


class Tenant(Base):
    """A customer account; one auth-provider org maps to one tenant (addendum §2, §3)."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    tier: Mapped[str] = mapped_column(String(32))  # TenantTier value
    control_chat_phone_e164: Mapped[str | None] = mapped_column(String(20), default=None)


class ChannelConfig(TimestampedTenantBase):
    """Per-tenant channel configuration (per-channel credentials and settings)."""

    __tablename__ = "channel_configs"
    __table_args__ = (UniqueConstraint("kind", "external_id", name="uq_channel_kind_extid"),)

    kind: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)


class Source(TimestampedTenantBase):
    """A watched conversation thread; opt-out model via ``excluded`` (addendum §4)."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("tenant_id", "thread_id", name="uq_sources_tenant_thread"),)

    thread_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # SourceKind value
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    excluded: Mapped[bool] = mapped_column(default=False)


class Message(TimestampedTenantBase):
    """A raw inbound/outbound message; ``external_id`` is unique per tenant (idempotency, §5)."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_messages_tenant_extid"),
    )

    external_id: Mapped[str] = mapped_column(String(128))
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="whatsapp")
    sender_phone_e164: Mapped[str] = mapped_column(String(20))
    sender_display_name: Mapped[str | None] = mapped_column(String(255), default=None)
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


class UnclaimedDelivery(Base):
    """Complete webhook change received for an endpoint with no tenant configuration.

    This is operational quarantine data, not a business row: no tenant is known yet. It is kept
    outside tenant-scoped tables until an operator claims and replays it.
    """

    __tablename__ = "unclaimed_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[str | None] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Contact(TimestampedTenantBase):
    """A known person the system has interacted with."""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("tenant_id", "phone_e164", name="uq_contacts_tenant_phone"),)

    full_name: Mapped[str | None] = mapped_column(String(255), default=None)
    phone_e164: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255), default=None)
    alternate_phones: Mapped[list[str]] = mapped_column(JSON, default=list)
    external_system: Mapped[str | None] = mapped_column(String(64), default=None)
    external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Conversation(TimestampedTenantBase):
    """An active or completed conversation thread."""

    __tablename__ = "conversations"

    channel: Mapped[str] = mapped_column(String(32))
    channel_thread_id: Mapped[str] = mapped_column(String(128))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("contacts.id"),
        default=None,
    )
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(8), default=None)
    status: Mapped[str] = mapped_column(String(16), default="open")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_turn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Turn(TimestampedTenantBase):
    """One inbound or outbound turn in a conversation."""

    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_turns_idempotency"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id"),
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(32))
    modality: Mapped[str] = mapped_column(String(16))
    body_text: Mapped[str | None] = mapped_column(Text, default=None)
    speech_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TaskRow(TimestampedTenantBase):
    """Persisted task state; named TaskRow to avoid collision with the in-memory Task dataclass."""

    __tablename__ = "task_rows"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id"),
        index=True,
    )
    intent: Mapped[str] = mapped_column(String(64))
    slots: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    slots_confirmed: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="collecting")
    outcome_ref: Mapped[str | None] = mapped_column(String(255), default=None)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class UnderstandingRow(TimestampedTenantBase):
    """The LLM's parse of a single turn."""

    __tablename__ = "understandings"

    turn_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("turns.id"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("task_rows.id"),
        default=None,
    )
    intent: Mapped[str] = mapped_column(String(64))
    slots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence_overall: Mapped[float] = mapped_column()
    confidence_intent: Mapped[float] = mapped_column()
    confidence_person: Mapped[float] = mapped_column()
    confidence_company: Mapped[float] = mapped_column()
    autonomy: Mapped[str | None] = mapped_column(String(16), default=None)
    model_used: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CorrectionRow(TimestampedTenantBase):
    """A human correction applied to an understanding."""

    __tablename__ = "corrections"

    understanding_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("understandings.id"),
    )
    original_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    corrected_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    corrected_via: Mapped[str | None] = mapped_column(String(64), default=None)
    promoted_to_golden: Mapped[bool] = mapped_column(Boolean, default=False)


class Property(TimestampedTenantBase):
    """One rental unit a tenant manages (roadmap 2.8). A client is rarely a single flat: it is an
    agency with many, and a fact ("the wifi password is…", "parking is on the east side") is true
    of one of them, not all. This table is what a ``facts`` row can be scoped to.

    ``external_id`` is the property's id in the operator's own property-management system, when
    there is one — the join key roadmap 3.1's booking lookup will resolve a guest's stay against.
    Null until a PMS is connected; the table is useful before then, because a fact can be scoped to
    a property this system named locally without any PMS knowing it exists.

    ``timezone`` is per-property rather than per-tenant because an agency can span cities (the
    emergency path's ``only_between`` window is read from the tenant policy today, G3 — a property
    clock is where a multi-city tenant would eventually resolve it from).
    """

    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_properties_tenant_extid"),
    )

    name: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    timezone: Mapped[str | None] = mapped_column(String(64), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FactRow(TimestampedTenantBase):
    """One row of a tenant's knowledge base (roadmap 2.4). Named ``FactRow`` to avoid colliding
    with the in-memory ``Fact`` dataclass, the same way ``TaskRow`` sits beside ``Task``.

    ``sensitive`` is unenforced outside ``answer_from_knowledge`` (``core/knowledge.py``) — the
    reply-path-wide gate is roadmap G1, not yet built.

    ``property_id`` scopes a fact to one unit (roadmap 2.8). ``NULL`` is deliberate and common: it
    means the fact is true of every property the tenant runs ("office hours are 9–5"), so the
    knowledge lookup returns tenant-wide facts *and* the resolved property's, never a property's
    facts to a message about a different one. See ``db/knowledge_repo.py``.
    """

    __tablename__ = "facts"

    topic: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("properties.id"), default=None, index=True
    )


class UsageEvent(Base):
    """Metered usage event; not tenant-scoped via the base class (uses BIGSERIAL PK)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    period: Mapped[datetime] = mapped_column(Date)
    metric: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[float | None] = mapped_column(Float, default=None)
    ref_id: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ClinicBranch(TimestampedTenantBase):
    """One location of a clinic tenant (demo step 3; domain object ``core/clinic.py``'s ``Branch``).

    The clinic tables are named ``clinic_*`` rather than ``branches``/``services``: this schema is
    shared by every vertical, and a table called ``services`` in it would read as the product's own
    services. The ORM classes carry the same prefix, which is also what keeps them from colliding
    with the domain dataclasses the way ``FactRow`` does with ``Fact``.

    ``external_id`` is the branch's identifier in the client's own records — the join key the
    workbook's availability rows name, and the one Salesforce will name later. It is the natural
    key an import upserts on, so it is unique per tenant and not null.

    ``placeholder`` marks a branch whose real details have not been supplied (five of the client's
    fourteen). Recorded, not hidden: decision 5 puts all fourteen in the demo.
    """

    __tablename__ = "clinic_branches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_clinic_branches_tenant_extid"),
    )

    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    area: Mapped[str | None] = mapped_column(String(255), default=None)
    address: Mapped[str | None] = mapped_column(Text, default=None)
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    timezone: Mapped[str | None] = mapped_column(String(64), default=None)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    import_version: Mapped[str | None] = mapped_column(String(64), default=None)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ClinicService(TimestampedTenantBase):
    """One treatment or package in a clinic's catalogue (``core/clinic.py``'s ``Service``).

    ``price_minor`` is an integer in the currency's minor unit (piastres for EGP). A price that is
    quoted to a patient is not a float: the vocabulary's ``quoting`` block forbids inventing or
    drifting an amount, and binary rounding is drift nobody authored.

    ``session_count`` is the package quantity — one Primelase session and a six-session package are
    two rows of the same modality at different quantities, and every quote has to say which.

    ``aliases`` holds the other names the same service is called by; the importer resolves an
    availability row's free-text service name through them. Without it the ambiguous names in the
    source workbook ("Basic Facial" and "Facial" at the same price and duration) have no canonical
    id behind them, and the assistant burns its two clarifying turns on a distinction the
    catalogue does not actually make.

    ``import_version``/``imported_at`` are the provenance the clinic vocabulary's
    ``imported_catalogue`` system promises: "every returned item retains its source identifier,
    import version and fetched time". The source identifier is ``code``.
    """

    __tablename__ = "clinic_services"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_clinic_services_tenant_code"),)

    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64), default=None)
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    session_count: Mapped[int] = mapped_column(Integer, default=1)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    import_version: Mapped[str | None] = mapped_column(String(64), default=None)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ClinicAvailabilitySlot(TimestampedTenantBase):
    """One bookable interval, in one branch, for one service (``AvailabilitySlot``).

    ``starts_at``/``ends_at`` are stored timezone-aware. The workbook holds a wall clock and a date
    and names no zone, so the zone is an explicit argument to the import and is never defaulted —
    ``TENANT_TIMEZONE`` still ships defaulting to ``Asia/Dubai``, one hour off the demo's Cairo.

    ``held_until``/``held_by_conversation_id`` are step 6's (``hold_slot``) and are untouched by
    the import. They are in migration 008 rather than a later one because the booking journey is
    the next thing built and a second migration to add two columns to a table nothing has read yet
    is a deploy for no reason. Nothing reads them today.

    A slot's ``status`` is the workbook's own: 407 Open, 265 Booked at the last import. Adjacent
    slots with no gap between them are accepted as-is (decision 3); the 15-minute buffer applies to
    new bookings, not to what is already in the diary.
    """

    __tablename__ = "clinic_availability_slots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_clinic_slots_tenant_extid"),
    )

    external_id: Mapped[str] = mapped_column(String(64))
    branch_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clinic_branches.id"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("clinic_services.id"), index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="open")
    held_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    held_by_conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    import_version: Mapped[str | None] = mapped_column(String(64), default=None)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ClinicBooking(TimestampedTenantBase):
    """An appointment against one slot (``core/clinic.py``'s ``Booking``).

    Two uniqueness constraints, and they defend against different mistakes.

    ``(tenant_id, slot_id)`` is the one that matters clinically: a slot holds one appointment, so
    two conversations racing for the last 18:00 cannot both be told they have it. Note what it
    also means — a cancelled booking still occupies its slot. Cancellation is a hand-off in the
    shipped vocabulary (there is no cancel tool), so nothing in the demo path needs to rebook a
    released slot, and a constraint that is simple to reason about is worth more here than one that
    anticipates a flow that does not exist.

    ``(tenant_id, idempotency_key)`` is what makes step 6's confirm retry-safe: the key is built
    from tenant, conversation and slot (``core/clinic.booking_idempotency_key``), so the same
    conversation confirming the same slot twice writes one row. Imported bookings carry no key —
    they have no conversation — and NULLs do not collide in a unique index, which is the intended
    behaviour and the reason the constraint is not a substitute for the one above.

    ``branch`` and ``service`` are deliberately absent: they belong to the slot, and a copy here is
    a second answer to the same question waiting to disagree with the first.
    """

    __tablename__ = "clinic_bookings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference", name="uq_clinic_bookings_tenant_reference"),
        UniqueConstraint("tenant_id", "slot_id", name="uq_clinic_bookings_tenant_slot"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_clinic_bookings_tenant_idempotency"
        ),
    )

    reference: Mapped[str] = mapped_column(String(32))
    slot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("clinic_availability_slots.id"), index=True
    )
    patient_name: Mapped[str | None] = mapped_column(String(255), default=None)
    patient_phone: Mapped[str | None] = mapped_column(String(20), default=None)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    source: Mapped[str] = mapped_column(String(16), default="workbook")
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id"), default=None, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    import_version: Mapped[str | None] = mapped_column(String(64), default=None)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
