"""Inbound message envelope — the normalized form of a Meta webhook payload (addendum §4, §5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import PhoneE164
from apps.api.schemas.enums import MessageDirection, MessageType, SourceKind


class MessageEnvelope(BaseModel):
    """A single WhatsApp message, normalized from the raw Meta payload before classification.

    Persisted to ``messages`` *before* enqueuing so a worker crash never loses a message
    (at-least-once + idempotency on ``wa_message_id`` ≈ exactly once — addendum §5).
    """

    # The raw Meta payload carries many fields we don't model; ignore extras rather than reject.
    model_config = ConfigDict(extra="ignore")

    wa_message_id: str = Field(
        description="Meta's message id; unique per tenant (idempotency key, §5)."
    )
    wa_chat_id: str = Field(description="Conversation id used to assemble history (addendum §7).")
    source_kind: SourceKind
    sender_phone_e164: PhoneE164
    sender_wa_name: str | None = None
    direction: MessageDirection = MessageDirection.INBOUND
    type: MessageType
    body_text: str | None = Field(default=None, description="Text content for text messages.")
    media_id: str | None = Field(
        default=None, description="Meta media id for audio/image/document (§6)."
    )
    media_mime: str | None = None
    transcript_text: str | None = Field(
        default=None,
        description="Transcribed/OCR'd text for non-text messages; classified like body_text (§6).",
    )
    received_at: datetime
    raw_payload: dict[str, Any] = Field(
        default_factory=dict, description="Original Meta envelope (jsonb)."
    )

    @property
    def classifiable_text(self) -> str | None:
        """Text fed to the classifier: the body for text, else the extracted transcript (§6)."""
        return self.body_text or self.transcript_text
