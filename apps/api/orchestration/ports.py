"""Ports the orchestrator depends on (addendum §4, §9, §12).

The orchestrator decides *what* happens to a message; these seams supply rules/cache and persist the
inbox item. Actual destination delivery and control-chat pings are driven from the returned plan by
their own already-built modules — kept out of here so the routing decision stays unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.api.identity.models import CrmRecord
from apps.api.identity.resolver import IncomingContact
from apps.api.rules.models import Rule
from apps.api.schemas.enums import ConfidenceBand, InboxStatus

# tenant_id → that tenant's enabled rules (priority order handled by the engine).
RulesProvider = Callable[[str], list[Rule]]

# (tenant_id, incoming contact) → candidate cached records to dedup against (§9, crm_cache).
CrmLookup = Callable[[str, IncomingContact], list[CrmRecord]]


@dataclass(frozen=True, slots=True)
class InboxItemDraft:
    """What the orchestrator hands the inbox store to persist (addendum §4 ``inbox_items``)."""

    tenant_id: str
    message_id: str
    status: InboxStatus
    band: ConfidenceBand | None
    model_used: str | None
    assigned_destination_id: str | None
    snapshot: dict[str, Any] = field(default_factory=dict)


class InboxWriter(Protocol):
    """Persists an inbox item for the control page."""

    def create(self, draft: InboxItemDraft) -> None: ...
