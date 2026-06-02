"""Ports for the media pipeline (addendum §6, D3-a).

The concrete implementations are a §17 decision: Whisper API vs self-hostable faster-whisper for
ASR; the primary LLM's vision vs Tesseract for OCR. The pipeline depends only on these interfaces,
so the provider choice is a config swap and the orchestration is testable without model or network.
"""

from __future__ import annotations

from typing import Protocol


class MediaDownloader(Protocol):
    """Fetches media bytes from Meta into tenant-scoped storage (addendum §6)."""

    def download(self, tenant_id: str, media_id: str) -> bytes: ...


class Transcriber(Protocol):
    """Transcribes audio (voice notes) to text — Arabic + dialects (addendum §6)."""

    def transcribe(self, audio: bytes, mime: str | None) -> str: ...


class VisionExtractor(Protocol):
    """Extracts text from images/PDFs via vision or OCR (addendum §6)."""

    def extract_text(self, document: bytes, mime: str | None) -> str: ...
