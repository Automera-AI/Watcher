"""Audit entry schema and the write port (addendum §4 ``audit_log``).

Every routing action — by the bot (rules/auto-route) or a user (inbox/control-chat) — appends an
entry with a snapshot of the classification and the resulting destination record, keeping the trail
complete.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AuditEntry(BaseModel):
    """One append-only audit record."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    message_id: str
    action: str  # e.g. "auto_routed", "confirmed", "skipped"
    actor: str  # "bot" or a user id
    classification_snapshot: dict[str, Any] = Field(default_factory=dict)
    destination_id: str | None = None
    destination_record_id: str | None = None
    destination_record_url: str | None = None


class AuditLog(Protocol):
    """Append-only sink for audit entries."""

    def write(self, entry: AuditEntry) -> None: ...
