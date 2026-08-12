"""The nine things the receptionist can do. There is no tenth.

Every tool is safe to call twice with the same key. That matters because networks retry, Meta
re-delivers webhooks, and a guest who is booked twice is a refund and a bad review.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]
    human_summary: str
    error: str | None = None


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    requires_verified_identity: ClassVar[bool] = False
    #: Longest we are prepared to wait. Anything over this and we tell the customer we will come
    #: back to them rather than leaving them listening to silence on a phone call.
    budget_ms: ClassVar[int] = 2000

    @abstractmethod
    async def run(self, tenant_id: UUID, idempotency_key: str, **kwargs: Any) -> ToolResult: ...


REGISTRY: dict[str, type[Tool]] = {}


def register(cls: type[Tool]) -> type[Tool]:
    if cls.name in REGISTRY:
        raise ValueError(f"tool {cls.name} registered twice")
    REGISTRY[cls.name] = cls
    return cls


@register
class TakeMessage(Tool):
    """The safe default. When in doubt, this. It is never wrong to take a message."""

    name = "take_message"
    description = "Write down what the customer wants so a human can pick it up."
    budget_ms = 300

    async def run(self, tenant_id: UUID, idempotency_key: str, **kwargs: Any) -> ToolResult:
        return ToolResult(
            ok=True,
            data={"message_id": idempotency_key},
            human_summary="Message taken. Someone will get back to you shortly.",
        )


@register
class HandoffToHuman(Tool):
    name = "handoff_to_human"
    description = "Pass this conversation to a person, with everything gathered so far."
    budget_ms = 300

    async def run(self, tenant_id: UUID, idempotency_key: str, **kwargs: Any) -> ToolResult:
        return ToolResult(
            ok=True,
            data={"handoff_id": idempotency_key, "reason": kwargs.get("reason", "unspecified")},
            human_summary="Putting you through to a colleague now.",
        )


# Phase B fills in the rest. The names are fixed now so the prompt, the database and the
# Control Page can all be written against them.
PLANNED = (
    "lookup_reservation",
    "check_availability",
    "hold_slot",
    "confirm_booking",
    "send_otp",
    "verify_otp",
    "answer_from_knowledge",
    "create_ticket",
)
