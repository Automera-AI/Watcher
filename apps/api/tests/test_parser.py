"""Tests for the Meta webhook payload parser (addendum §5)."""

from __future__ import annotations

from typing import Any

from apps.api.ingestion.parser import iter_change_values, parse_webhook
from apps.api.schemas.enums import MessageType


def _payload(
    *messages: dict[str, Any], statuses: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "+15550000000", "phone_number_id": "PNID"},
        "contacts": [{"profile": {"name": "Sara"}, "wa_id": "966500000000"}],
        "messages": list(messages),
    }
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}],
    }


def test_parses_text_message() -> None:
    payload = _payload(
        {
            "from": "966500000000",
            "id": "wamid.A",
            "timestamp": "1700000000",
            "type": "text",
            "text": {"body": "Need a quote"},
        }
    )
    [msg] = parse_webhook(payload)
    assert msg.wa_message_id == "wamid.A"
    assert msg.type is MessageType.TEXT
    assert msg.body_text == "Need a quote"
    assert msg.sender_phone_e164 == "+966500000000"  # bare wa_id rendered E.164
    assert msg.sender_wa_name == "Sara"


def test_status_events_are_ignored() -> None:
    payload = _payload(statuses=[{"id": "wamid.X", "status": "delivered"}])
    assert parse_webhook(payload) == []


def test_audio_message_captures_media_not_body() -> None:
    payload = _payload(
        {
            "from": "966500000000",
            "id": "wamid.V",
            "timestamp": "1700000000",
            "type": "audio",
            "audio": {"id": "media-1", "mime_type": "audio/ogg"},
        }
    )
    [msg] = parse_webhook(payload)
    assert msg.type is MessageType.AUDIO
    assert msg.media_id == "media-1"
    assert msg.media_mime == "audio/ogg"
    assert msg.body_text is None


def test_image_caption_becomes_body_text() -> None:
    payload = _payload(
        {
            "from": "966500000000",
            "id": "wamid.I",
            "timestamp": "1700000000",
            "type": "image",
            "image": {"id": "media-2", "mime_type": "image/jpeg", "caption": "business card"},
        }
    )
    [msg] = parse_webhook(payload)
    assert msg.type is MessageType.IMAGE
    assert msg.body_text == "business card"


def test_unknown_type_maps_to_other() -> None:
    payload = _payload(
        {"from": "966500000000", "id": "wamid.L", "timestamp": "1700000000", "type": "location"}
    )
    [msg] = parse_webhook(payload)
    assert msg.type is MessageType.OTHER


def test_iter_change_values_exposes_phone_number_id() -> None:
    payload = _payload(
        {
            "from": "966500000000",
            "id": "wamid.A",
            "timestamp": "1700000000",
            "type": "text",
            "text": {"body": "hi"},
        }
    )
    [(phone_number_id, _value)] = list(iter_change_values(payload))
    assert phone_number_id == "PNID"
