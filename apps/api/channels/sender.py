"""Channel sender protocol — the contract for delivering an outbound action."""

from __future__ import annotations

from typing import Protocol

from apps.api.schemas.envelope import InboundTurn, OutboundAction


class ChannelSender(Protocol):
    """Delivers an outbound action via the appropriate channel."""

    async def send(self, action: OutboundAction, turn: InboundTurn) -> None: ...

    def close(self) -> None:
        """Release whatever the adapter holds open. Called at application shutdown.

        On the protocol rather than on the one implementation that needs it today: every adapter
        that reaches a network holds a connection pool, and a process that does not close them is
        a container that does not exit.
        """
        ...
