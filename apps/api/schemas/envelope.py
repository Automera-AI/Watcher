"""The two shapes every channel must speak (ported from the v2 scaffold, roadmap 1.2).

This is the contract between `apps/api/channels/` and the core. Nothing else crosses that line.
If a channel needs to pass something new through, it goes here, in a form that is meaningful for
every channel and not just the one that needed it.

**One deliberate change from the scaffold: the three-button cap is gone from here.** The scaffold
raised on a fourth option inside ``OutboundAction`` — a per-channel number enforced in the
channel-agnostic core, which is the exact mistake the boundary test exists to prevent. It is
worth being precise about how well it hid: the scaffold's own ``test_boundary.py`` passes on that
file, because it only catches provider names used as identifiers and the offence was inside a
string literal.

Three is also simply the wrong number everywhere else. A phone call has no buttons; a web widget
has no particular limit. Capping in the core would permanently pin every future channel to the
most restrictive one. So the cap moved to ``channels/whatsapp.py``, where it is true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

Channel = Literal["whatsapp", "voice", "web", "email", "instagram"]
Modality = Literal["text", "audio", "keypad", "button"]

ActionKind = Literal["say", "ask", "confirm", "handoff", "end", "send_template", "collect_keypad"]


@dataclass(frozen=True, slots=True)
class InboundTurn:
    """One thing a customer said, normalised.

    Note the field names: ``channel_thread_id`` and ``channel_identity`` — a conversation id and
    a sender id that mean something on a phone call as well as in a chat. This is the shape
    roadmap 1.1 is moving the rest of the core towards.
    """

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
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.modality in ("text", "audio") and self.text is None:
            raise ValueError("text and audio turns must carry text")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required; it is what stops double handling")


@dataclass(frozen=True, slots=True)
class OutboundAction:
    """One thing the receptionist wants to do back.

    Channels ignore the fields that do not apply to them. A phone call ignores ``quick_replies``;
    WhatsApp ignores ``speech_hint``. Neither should ever raise because of it — and neither
    should this class raise because of a limit that belongs to one channel.
    """

    kind: ActionKind
    text: str
    speech_hint: dict[str, Any] | None = None
    quick_replies: list[str] | None = None
    template_name: str | None = None
    requires_ack: bool = False

    def __post_init__(self) -> None:
        if self.kind == "send_template" and not self.template_name:
            raise ValueError("send_template needs template_name")
