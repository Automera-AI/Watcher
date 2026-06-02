"""Tests for Meta webhook HMAC verification (addendum §5)."""

from __future__ import annotations

from apps.api.ingestion.security import expected_signature, verify_signature

SECRET = "app-secret"
PAYLOAD = b'{"object":"whatsapp_business_account"}'


def test_valid_signature_passes() -> None:
    header = expected_signature(SECRET, PAYLOAD)
    assert verify_signature(SECRET, PAYLOAD, header)


def test_tampered_payload_fails() -> None:
    header = expected_signature(SECRET, PAYLOAD)
    assert not verify_signature(SECRET, PAYLOAD + b"x", header)


def test_wrong_secret_fails() -> None:
    header = expected_signature("other-secret", PAYLOAD)
    assert not verify_signature(SECRET, PAYLOAD, header)


def test_missing_or_malformed_header_fails() -> None:
    assert not verify_signature(SECRET, PAYLOAD, None)
    assert not verify_signature(SECRET, PAYLOAD, "")
    assert not verify_signature(SECRET, PAYLOAD, "deadbeef")  # no sha256= prefix
