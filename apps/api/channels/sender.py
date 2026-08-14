"""Channel sender protocol — the contract for delivering an outbound action."""

from __future__ import annotations

from typing import Protocol

from apps.api.schemas.envelope import InboundTurn, OutboundAction


class ChannelSender(Protocol):
    """Delivers an outbound action via the appropriate channel."""

    async def send(self, action: OutboundAction, turn: InboundTurn) -> None: ...
