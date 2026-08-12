"""Phone adapter. Phase C.

Left here deliberately so the shape is visible from day one. When you build it, the only thing
that changes anywhere else in the codebase is that this class gets a real body. If you find
yourself needing to touch `app/core/` to make phone calls work, stop, because something has
leaked across the boundary.
"""

from __future__ import annotations

from uuid import UUID

from app.channels.base import ChannelAdapter
from app.core.envelope import InboundTurn, OutboundAction


class VoiceAdapter(ChannelAdapter):
    name = "voice"

    async def normalise(self, payload: dict, tenant_id: UUID) -> list[InboundTurn]:
        raise NotImplementedError("Phase C")

    async def render(self, action: OutboundAction, turn: InboundTurn) -> None:
        raise NotImplementedError("Phase C")
