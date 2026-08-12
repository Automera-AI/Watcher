"""Every channel implements exactly these two methods. Nothing more."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.core.envelope import Channel, InboundTurn, OutboundAction


class ChannelAdapter(ABC):
    name: Channel

    @abstractmethod
    async def normalise(self, payload: dict, tenant_id: UUID) -> list[InboundTurn]:
        """Turn whatever the provider sent us into zero or more InboundTurns.

        Returns a list because one webhook delivery can contain several messages.
        Must be safe to call twice with the same payload.
        """

    @abstractmethod
    async def render(self, action: OutboundAction, turn: InboundTurn) -> None:
        """Send the reply. Must be safe to call twice for the same action."""

    async def verify(self, headers: dict, body: bytes) -> bool:
        """Confirm the request really came from the provider. Default: reject."""
        return False
