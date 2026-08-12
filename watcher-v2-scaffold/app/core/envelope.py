"""The two shapes every channel must speak.

This file is the contract between `app/channels/` and `app/core/`. Nothing else crosses that
line. If a channel needs to pass something new through, it goes here, in a form that is
meaningful for every channel and not just the one that needed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

Channel = Literal["whatsapp", "voice", "web", "email", "instagram"]
Modality = Literal["text", "audio", "keypad", "button"]


@dataclass(frozen=True, slots=True)
class InboundTurn:
    """One thing a customer said, normalised."""

    tenant_id: UUID
    channel: Channel
    channel_thread_id: str
    channel_identity: str
    modality: Modality
    text: str | None
    received_at: datetime
    idempotency_key: str
    speech_confidence: float | None = None
    media_ref: str | None = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.modality in ("text", "audio") and self.text is None:
            raise ValueError("text and audio turns must carry text")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required; it is what stops double handling")


ActionKind = Literal[
    "say", "ask", "confirm", "handoff", "end", "send_template", "collect_keypad"
]


@dataclass(frozen=True, slots=True)
class OutboundAction:
    """One thing the receptionist wants to do back.

    Channels ignore the fields that do not apply to them. A phone call ignores `quick_replies`;
    WhatsApp ignores `speech_hint`. Neither should ever raise because of it.
    """

    kind: ActionKind
    text: str
    speech_hint: dict | None = None
    quick_replies: list[str] | None = None
    template_name: str | None = None
    requires_ack: bool = False

    def __post_init__(self) -> None:
        if self.kind == "send_template" and not self.template_name:
            raise ValueError("send_template needs template_name")
        if self.quick_replies and len(self.quick_replies) > 3:
            raise ValueError("WhatsApp allows at most 3 quick reply buttons")
