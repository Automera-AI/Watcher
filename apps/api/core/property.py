"""Resolving which property a message is about (roadmap 2.8).

A client is an agency with many units, and a knowledge-base fact is usually true of one of them,
not all (``db/models.py``'s ``FactRow.property_id``). Before the lookup can scope facts to a
property it has to decide *which* property the guest in front of it is asking about — and today,
with slot extraction (item 2.x) and the PMS booking lookup (roadmap 3.1) both unbuilt, the signals
available to make that call are deliberately few. This module is honest about which two they are.

**The resolution order, and why each step is the shape it is.**

1. **An explicit hint wins and is authoritative.** When the caller already knows the property —
   an endpoint wired to a single unit passes the unit's id or ``external_id`` as the hint —
   that answer is used and the fallback below is *not* consulted. A hint that names no active
   property of the tenant resolves to ``None`` rather than guessing past a caller who was specific:
   a misconfigured number should degrade to tenant-wide facts, not silently answer for whichever
   other unit happens to be the only active one.

2. **One active property means every message is about it.** The overwhelmingly common early case
   is a client with a single unit; there is nothing to disambiguate, so the message resolves to it
   without any per-message signal at all.

3. **Otherwise, ``None`` — tenant-wide facts only.** With many units and no hint, this system
   cannot yet tell which one a guest on a shared number means. Returning ``None`` scopes the lookup
   to facts true of every unit (``property_id IS NULL``) and leaves the rest to a genuine "I don't
   know" handoff, which is the same trade ``core/knowledge.py`` already makes. Guessing a unit here
   is how one property's parking instructions reach a guest staying in another.

The per-message *hint* is not wired through the receptionist yet — no channel carries a
property-scoped endpoint today — so step 2 is the path that runs in production and steps 1 and 3
are exercised by tests. When a client runs a number per unit, the hint is the endpoint's configured
property id, plumbed from ``channel_configs`` the same way the tenant id already is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Property:
    """One rental unit a tenant manages (``db/models.py``'s ``Property`` row)."""

    id: str
    name: str
    external_id: str | None = None
    timezone: str | None = None


class PropertyResolver(Protocol):
    """Where a tenant's properties live, and the resolution over them. One implementation today:
    ``db.property_repo.SqlAlchemyPropertyRepository``."""

    def resolve(self, tenant_id: str, *, hint: str | None = None) -> str | None: ...


def resolve_property(properties: list[Property], hint: str | None = None) -> str | None:
    """The pure resolution policy over a tenant's active properties. See the module docstring.

    Kept separate from the repository that fetches ``properties`` for the same reason
    ``core.knowledge.best_match`` is separate from ``SqlAlchemyFactRepository``: the decision is
    testable without a database, and the database code is a thin fetch.
    """
    if hint is not None:
        for prop in properties:
            if hint in (prop.id, prop.external_id):
                return prop.id
        return None
    if len(properties) == 1:
        return properties[0].id
    return None
