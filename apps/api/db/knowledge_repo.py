"""Repository for the knowledge base (roadmap 2.4)."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.sql.elements import ColumnElement

from apps.api.core.knowledge import Fact
from apps.api.db.engine import TenantScope
from apps.api.db.models import FactRow


class SqlAlchemyFactRepository:
    """A tenant's active facts (``KnowledgeLookup``, ``core/knowledge.py``).

    Inactive rows (``active=False``) are excluded here rather than filtered at match time — a
    fact someone has retired from the knowledge view (roadmap D5) should never be a candidate,
    not merely a low-scoring one.

    ``property_id`` scopes the result to one unit (roadmap 2.8): a fact whose ``property_id`` is
    ``NULL`` is true of every property the tenant runs and is always returned; a property-scoped
    fact is returned only when it matches the resolved property. Passing ``None`` — the caller
    could not resolve a property — returns tenant-wide facts alone, never a specific unit's, so a
    message on a shared number can never be answered from the wrong property's sheet.
    """

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def search(self, tenant_id: str, property_id: str | None = None) -> list[Fact]:
        scope_clause: ColumnElement[bool] = FactRow.property_id.is_(None)
        if property_id is not None:
            scope_clause = or_(scope_clause, FactRow.property_id == uuid.UUID(property_id))
        with self._scope(tenant_id) as session:
            rows = (
                session.execute(
                    select(FactRow).where(
                        FactRow.tenant_id == uuid.UUID(tenant_id),
                        FactRow.active.is_(True),
                        scope_clause,
                    )
                )
                .scalars()
                .all()
            )
            return [
                Fact(
                    id=str(row.id),
                    topic=row.topic,
                    question=row.question,
                    answer=row.answer,
                    sensitive=row.sensitive,
                    property_id=str(row.property_id) if row.property_id is not None else None,
                )
                for row in rows
            ]
