"""Declarative base and common columns (addendum §3, §4).

Every business table carries ``tenant_id``. In Postgres, isolation is enforced by Row-Level Security
plus an application-layer filter (defense in depth, §3); the ORM models just declare the column.
SQLite (used in tests) has no RLS — tenant scoping there relies on the query filter alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class TimestampedTenantBase(Base):
    """Abstract mixin: UUID primary key, ``tenant_id``, and a ``created_at`` timestamp."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
