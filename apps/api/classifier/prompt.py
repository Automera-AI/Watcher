"""Prompt construction and the structured-output schema (addendum §8).

Every classification stores ``PROMPT_VERSION``; the eval tool keys regressions to it (§8, §13).
Bump it on any change to the prompt text or output schema. Concrete providers use
``CLASSIFICATION_TOOL_SCHEMA`` for constrained decoding / tool-call mode.
"""

from __future__ import annotations

from typing import Any

from apps.api.classifier.types import ClassificationInput
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import MessageType

# Bump on any prompt-text or output-schema change (§8).
PROMPT_VERSION = "v1"

# JSON Schema of the required structured output; providers bind this as the tool/response schema.
CLASSIFICATION_TOOL_SCHEMA: dict[str, Any] = ClassificationResult.model_json_schema()

SYSTEM_PROMPT = (
    "You classify inbound business WhatsApp messages for a CRM. Read the conversation history and "
    "the latest message, then return ONLY the structured fields. Messages may be Arabic, English, "
    "or mixed. Report calibrated confidences in [0,1]; lower them when a message is noisy."
)

# Non-text messages arrive as transcribed/OCR'd text (§6); tell the model so it calibrates.
_MODALITY_NOTE = {
    MessageType.AUDIO: " (transcribed from a voice note; transcription may contain errors)",
    MessageType.IMAGE: " (text extracted from an image; OCR may contain errors)",
    MessageType.DOCUMENT: " (text extracted from a document)",
}


def render_user_prompt(value: ClassificationInput) -> str:
    """Render the user-turn content: prior history (oldest→newest) then the message to classify."""
    lines: list[str] = []
    if value.history:
        lines.append("Conversation history (oldest first):")
        lines.extend(f"  [{turn.role}] {turn.text}" for turn in value.history)
        lines.append("")
    note = _MODALITY_NOTE.get(value.modality, "")
    lines.append(f"Message to classify{note}:")
    lines.append(value.text)
    return "\n".join(lines)
