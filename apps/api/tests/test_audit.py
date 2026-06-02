"""Tests for the audit entry schema and write port (addendum §4)."""

from __future__ import annotations

from apps.api.audit.log import AuditEntry, AuditLog


class _InMemoryAuditLog:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def write(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


def test_audit_entry_defaults_and_write() -> None:
    log: AuditLog = _InMemoryAuditLog()
    entry = AuditEntry(
        tenant_id="t1",
        message_id="m1",
        action="auto_routed",
        actor="bot",
        destination_id="d1",
        destination_record_id="rec-1",
        destination_record_url="https://crm/rec-1",
    )
    log.write(entry)

    assert entry.classification_snapshot == {}  # default
    assert isinstance(log, _InMemoryAuditLog)
    assert log.entries == [entry]
