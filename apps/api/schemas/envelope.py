"""The two shapes every channel must speak (ported from the v2 scaffold, roadmap 1.2).

This is the contract between `apps/api/channels/` and the core. Nothing else crosses that line.
If a channel needs to pass something new through, it goes here, in a form that is meaningful for
every channel and not just the one that needed it.

**Two deliberate changes from the scaffold.**

*Pydantic, not a dataclass.* The scaffold used frozen dataclasses, which do not enforce their own
annotations: a `Literal` is a hint to the type checker and nothing at all at runtime. This is the
provider boundary — the one place untrusted external input arrives — so an adapter could normalise
a webhook into `channel="whatsap"`, or a `tenant_id` that is not a UUID, and everything downstream
would trust it. AGENTS.md is explicit that Pydantic v2 models are the single source of truth, and
every other model in this package already is one.

*The three-button cap is gone from here.* The scaffold raised on a fourth option inside
`OutboundAction` — a per-channel number applied in the channel-agnostic core, which is the exact
mistake the boundary test exists to prevent. It is worth being precise about how well it hid: the
scaffold's own `test_boundary.py` passes on that file, because it only catches provider names used
as identifiers and the offence was inside a string literal.

Three is also simply the wrong number everywhere else. A phone call has no buttons; a web widget
has no particular limit. Capping in the core would permanently pin every future channel to the
most restrictive one. So the cap moved to `channels/whatsapp.py`, where it is true.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Channel = Literal["whatsapp", "voice", "web", "email", "instagram"]
Modality = Literal["text", "audio", "keypad", "button"]

ActionKind = Literal["say", "ask", "confirm", "handoff", "end", "send_template", "collect_keypad"]


class InboundTurn(BaseModel):
    """One thing a customer said, normalised.

    Note the field names: ``channel_thread_id`` and ``channel_identity`` — a conversation id and a
    sender id that mean something on a phone call as well as in a chat. This is the shape roadmap
    1.1 is moving the rest of the core towards.

    ``extra="forbid"`` because an adapter passing a field the core does not model is a bug in the
    adapter, and silently dropping it is how a channel-specific detail leaks in unnoticed. Frozen
    because a turn is a record of what was said; nothing downstream should edit it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    channel: Channel
    channel_thread_id: str
    channel_identity: str
    modality: Modality
    text: str | None
    received_at: datetime
    idempotency_key: str
    speech_confidence: float | None = Field(default=None, ge=0, le=1)
    media_ref: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _coherent(self) -> InboundTurn:
        if self.modality in ("text", "audio") and self.text is None:
            raise ValueError("text and audio turns must carry text")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required; it is what stops double handling")
        return self


class OutboundAction(BaseModel):
    """One thing the receptionist wants to do back.

    Channels ignore the fields that do not apply to them. A phone call ignores ``quick_replies``;
    WhatsApp ignores ``speech_hint``. Neither should ever raise because of it — and neither should
    this class raise because of a limit that belongs to one channel.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ActionKind
    text: str
    speech_hint: dict[str, Any] | None = None
    quick_replies: list[str] | None = None
    template_name: str | None = None
    requires_ack: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> OutboundAction:
        if self.kind == "send_template" and not self.template_name:
            raise ValueError("send_template needs template_name")
        return self
