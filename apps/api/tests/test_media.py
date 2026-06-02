"""Tests for the media pipeline (addendum §6)."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.api.media.pipeline import MediaPipeline
from apps.api.schemas.enums import MessageType, SourceKind
from apps.api.schemas.message import MessageEnvelope

TENANT = "tenant-1"


class _Downloader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def download(self, tenant_id: str, media_id: str) -> bytes:
        self.calls.append((tenant_id, media_id))
        return b"bytes"


class _Transcriber:
    def transcribe(self, audio: bytes, mime: str | None) -> str:
        return "transcribed text"


class _Vision:
    def extract_text(self, document: bytes, mime: str | None) -> str:
        return "extracted text"


def _pipeline() -> tuple[MediaPipeline, _Downloader]:
    downloader = _Downloader()
    return MediaPipeline(downloader, _Transcriber(), _Vision()), downloader


def _message(msg_type: MessageType, *, media_id: str | None) -> MessageEnvelope:
    return MessageEnvelope(
        wa_message_id="wamid.A",
        wa_chat_id="966500000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+966500000000",
        type=msg_type,
        media_id=media_id,
        media_mime="audio/ogg",
        received_at=datetime.now(UTC),
    )


def test_audio_is_transcribed() -> None:
    pipeline, downloader = _pipeline()
    text = pipeline.extract_text(TENANT, _message(MessageType.AUDIO, media_id="m1"))
    assert text == "transcribed text"
    assert downloader.calls == [(TENANT, "m1")]


def test_image_uses_vision() -> None:
    pipeline, _ = _pipeline()
    assert (
        pipeline.extract_text(TENANT, _message(MessageType.IMAGE, media_id="m1"))
        == "extracted text"
    )


def test_document_uses_vision() -> None:
    pipeline, _ = _pipeline()
    assert (
        pipeline.extract_text(TENANT, _message(MessageType.DOCUMENT, media_id="m1"))
        == "extracted text"
    )


def test_text_message_extracts_nothing() -> None:
    pipeline, downloader = _pipeline()
    assert pipeline.extract_text(TENANT, _message(MessageType.TEXT, media_id=None)) is None
    assert downloader.calls == []


def test_media_type_without_media_id_extracts_nothing() -> None:
    pipeline, _ = _pipeline()
    assert pipeline.extract_text(TENANT, _message(MessageType.AUDIO, media_id=None)) is None


def test_enrich_fills_transcript_and_feeds_classifiable_text() -> None:
    pipeline, _ = _pipeline()
    original = _message(MessageType.AUDIO, media_id="m1")
    enriched = pipeline.enrich(TENANT, original)

    assert original.transcript_text is None  # original untouched (immutable copy)
    assert enriched.transcript_text == "transcribed text"
    assert enriched.classifiable_text == "transcribed text"  # flows into the classifier (§6)
