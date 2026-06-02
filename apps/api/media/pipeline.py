"""Media pipeline: turn a non-text message into classifiable text (addendum §6).

Voice notes are transcribed; images and PDFs go through vision/OCR. The extracted text is written to
``transcript_text`` and then flows into the *same* classifier as ``body_text`` — the prompt is told
the modality so it calibrates confidence for noisier ASR/OCR output (§6, classifier/prompt.py).
"""

from __future__ import annotations

from apps.api.media.ports import MediaDownloader, Transcriber, VisionExtractor
from apps.api.schemas.enums import MessageType
from apps.api.schemas.message import MessageEnvelope

_VISION_TYPES = (MessageType.IMAGE, MessageType.DOCUMENT)


class MediaPipeline:
    """Downloads media and extracts text, by modality (addendum §6)."""

    def __init__(
        self,
        downloader: MediaDownloader,
        transcriber: Transcriber,
        vision: VisionExtractor,
    ) -> None:
        self._downloader = downloader
        self._transcriber = transcriber
        self._vision = vision

    def extract_text(self, tenant_id: str, message: MessageEnvelope) -> str | None:
        """Return extracted text for a media message, or ``None`` if there's nothing to extract."""
        if message.type is MessageType.TEXT or message.media_id is None:
            return None
        data = self._downloader.download(tenant_id, message.media_id)
        if message.type is MessageType.AUDIO:
            return self._transcriber.transcribe(data, message.media_mime)
        if message.type in _VISION_TYPES:
            return self._vision.extract_text(data, message.media_mime)
        return None  # OTHER (video, sticker, location, …) — no extraction path in v1

    def enrich(self, tenant_id: str, message: MessageEnvelope) -> MessageEnvelope:
        """Return a copy of ``message`` with ``transcript_text`` filled when extraction applies."""
        text = self.extract_text(tenant_id, message)
        if text is None:
            return message
        return message.model_copy(update={"transcript_text": text})
