"""Persistence for webhook changes whose endpoint has not been claimed yet."""

from __future__ import annotations

from typing import Any

from apps.api.db.engine import SessionScope
from apps.api.db.models import UnclaimedDelivery


class SessionScopedUnclaimedDeliveryStore:
    """Write complete unclaimed changes through the non-tenant resolver session."""

    def __init__(self, session_scope: SessionScope) -> None:
        self._session_scope = session_scope

    def save(self, endpoint_id: str | None, payload: dict[str, Any], reason: str) -> None:
        with self._session_scope() as session:
            session.add(UnclaimedDelivery(endpoint_id=endpoint_id, payload=payload, reason=reason))
            session.commit()
