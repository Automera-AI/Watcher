"""Repository for the knowledge base (roadmap 2.4)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from apps.api.core.knowledge import Fact
from apps.api.db.engine import TenantScope
from apps.api.db.models import FactRow


class SqlAlchemyFactRepository:
    """A tenant's active facts (``KnowledgeLookup``, ``core/knowledge.py``).

    Inactive rows (``active=False``) are excluded here rather than filtered at match time — a
    fact someone has retired from the knowledge view (roadmap D5) should never be a candidate,
    not merely a low-scoring one.
    """

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def search(self, tenant_id: str) -> list[Fact]:
        with self._scope(tenant_id) as session:
            rows = (
                session.execute(
                    select(FactRow).where(
                        FactRow.tenant_id == uuid.UUID(tenant_id),
                        FactRow.active.is_(True),
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
                )
                for row in rows
            ]
