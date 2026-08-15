"""Input/output value objects for the classifier (addendum §7, §8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import MessageDirection, MessageType
from apps.api.schemas.message import MessageEnvelope


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """One prior turn in the same chat, oldest→newest (addendum §7)."""

    role: str  # "contact" | "business"
    text: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ClassificationInput:
    """What the model needs to classify one message: text, modality, history, and who sent it.

    The sender fields come from the channel rather than the message body. ``ClassificationResult``
    scores ``person_name`` and ``phone_e164``, and the golden set expects both to match the
    sender — which was unanswerable while the model was shown only the text. They are optional
    because a group thread or a withheld number legitimately has neither.
    """

    text: str
    modality: MessageType
    history: tuple[HistoryTurn, ...] = field(default_factory=tuple)
    sender_display_name: str | None = None
    sender_phone: str | None = None


@dataclass(frozen=True, slots=True)
class ClassificationOutcome:
    """Result of a tiered classification.

    ``result`` is ``None`` when the model returned schema-invalid output twice — the message is
    marked unclear and routed to the inbox (addendum §8).

    ``latency_ms`` and ``prompt_version`` are the two columns ``classifications`` wants that the
    service alone can answer for. They are *measured and reported*, not reconstructed downstream:
    latency is the wall clock across every attempt and every tier, because that is what the guest
    waited for, and the prompt version is the one this service was built with rather than whatever
    constant the writer happens to import. A row cannot be written honestly without both, which is
    why they live on the outcome and not on the persistence adapter.
    """

    result: ClassificationResult | None
    model_used: str
    escalated: bool
    attempts: int
    latency_ms: int = 0
    prompt_version: str = ""

    @property
    def is_unclear(self) -> bool:
        return self.result is None


def _role_for(direction: MessageDirection) -> str:
    return "business" if direction is MessageDirection.OUTBOUND else "contact"


def input_from(
    message: MessageEnvelope, history: Sequence[MessageEnvelope] = ()
) -> ClassificationInput:
    """Build a classifier input from the current message and its prior turns (oldest→newest)."""
    turns = tuple(
        HistoryTurn(role=_role_for(m.direction), text=m.classifiable_text or "", at=m.received_at)
        for m in history
    )
    return ClassificationInput(
        text=message.classifiable_text or "",
        modality=message.type,
        history=turns,
        sender_display_name=message.sender_display_name,
        sender_phone=message.sender_phone_e164,
    )
