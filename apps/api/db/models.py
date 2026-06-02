"""ORM models (addendum §4).

This slice defines the ingestion hot-path tables (``tenants``, ``sources``, ``messages``) so the
webhook's persist-before-enqueue has a real backing store. The remaining tables (classifications,
inbox_items, crm_cache, …) follow the same pattern and land with the slices that need them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base, TimestampedTenantBase


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
