"""Repository for a tenant's properties (roadmap 2.8).

Fetches the tenant's active ``properties`` and applies the pure resolution policy in
``core.property``. Inactive rows (``active=False``) are excluded here rather than at resolve time,
the same way ``SqlAlchemyFactRepository`` excludes retired facts: a unit an operator has taken off
the books should never be a candidate to resolve a message to, not merely an unlikely one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from apps.api.core.property import Property, resolve_property
from apps.api.db.engine import TenantScope
from apps.api.db.models import Property as PropertyRow


class SqlAlchemyPropertyRepository:
    """A tenant's active properties, and message → property resolution over them."""

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def list_active(self, tenant_id: str) -> list[Property]:
        with self._scope(tenant_id) as session:
            rows = (
                session.execute(
                    select(PropertyRow).where(
                        PropertyRow.tenant_id == uuid.UUID(tenant_id),
                        PropertyRow.active.is_(True),
                    )
                )
                .scalars()
                .all()
            )
            return [
                Property(
                    id=str(row.id),
                    name=row.name,
                    external_id=row.external_id,
                    timezone=row.timezone,
                )
                for row in rows
            ]

    def resolve(self, tenant_id: str, *, hint: str | None = None) -> str | None:
        """The property a message is about, or ``None`` for tenant-wide (``core.property``)."""
        return resolve_property(self.list_active(tenant_id), hint)
