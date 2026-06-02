"""Tests for the control-chat tokens and state machine (addendum §10)."""

from __future__ import annotations

from apps.api.control_chat.state import (
    ControlAction,
    build_button_id,
    handle_reply,
    parse_button_id,
)
from apps.api.control_chat.tokens import issue_token, verify_token

SECRET = "tenant-secret"
ITEM = "inbox-item-1"
CONTROL_PHONE = "+966500000000"


def test_token_roundtrip() -> None:
    token = issue_token(ITEM, SECRET, now=1000)
    assert verify_token(token, SECRET, now=1001) == ITEM


def test_expired_token_is_rejected() -> None:
    token = issue_token(ITEM, SECRET, ttl_seconds=60, now=1000)
    assert verify_token(token, SECRET, now=1000 + 61) is None


def test_tampered_or_wrong_secret_token_is_rejected() -> None:
    token = issue_token(ITEM, SECRET, now=1000)
    assert verify_token(token, "other-secret", now=1001) is None
    assert verify_token(token + "x", SECRET, now=1001) is None


def test_button_id_roundtrip() -> None:
    button_id = build_button_id(ControlAction.CONFIRM, ITEM, SECRET)
    parsed = parse_button_id(button_id)
    assert parsed is not None
    assert parsed.action is ControlAction.CONFIRM
    assert parsed.inbox_item_id == ITEM


def test_parse_rejects_malformed() -> None:
    assert parse_button_id("only|two") is None
    assert parse_button_id("bogus|item|token") is None  # unknown action


def test_handle_reply_accepts_bound_sender_and_valid_token() -> None:
    button_id = build_button_id(ControlAction.SKIP, ITEM, SECRET)
    result = handle_reply(button_id, CONTROL_PHONE, CONTROL_PHONE, SECRET)
    assert result.applied is True
    assert result.action is ControlAction.SKIP
    assert result.inbox_item_id == ITEM


def test_handle_reply_rejects_unbound_sender() -> None:
    button_id = build_button_id(ControlAction.CONFIRM, ITEM, SECRET)
    result = handle_reply(button_id, "+966511111111", CONTROL_PHONE, SECRET)
    assert result.applied is False
    assert result.reason == "unbound_sender"


def test_handle_reply_rejects_expired_token() -> None:
    button_id = build_button_id(ControlAction.CONFIRM, ITEM, SECRET)
    # Far in the future, past the default 15-min TTL.
    result = handle_reply(button_id, CONTROL_PHONE, CONTROL_PHONE, SECRET, now=2_000_000_000)
    assert result.applied is False
    assert result.reason == "invalid_or_expired_token"
