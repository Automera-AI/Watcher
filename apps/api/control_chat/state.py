"""Control-chat button encoding and the pending-action state machine (addendum §10).

A medium-confidence item sends a WhatsApp interactive message with Confirm / Change / Skip buttons.
Each button id encodes the action, the inbox item, and a signed token. An inbound reply is honored
only when the token verifies *and* the sender is the bound control-chat number — no action from an
unbound number (addendum §2, §10).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apps.api.control_chat.tokens import issue_token, verify_token

_FIELD_SEP = "|"


class ControlAction(StrEnum):
    CONFIRM = "confirm"
    CHANGE = "change"
    SKIP = "skip"


def build_button_id(action: ControlAction, inbox_item_id: str, secret: str) -> str:
    """Encode ``action|item_id|token`` for an interactive button."""
    token = issue_token(inbox_item_id, secret)
    return _FIELD_SEP.join([action.value, inbox_item_id, token])


@dataclass(frozen=True, slots=True)
class ParsedButton:
    action: ControlAction
    inbox_item_id: str
    token: str


def parse_button_id(button_id: str) -> ParsedButton | None:
    """Parse a button id back into its parts, or ``None`` if malformed."""
    parts = button_id.split(_FIELD_SEP)
    if len(parts) != 3:
        return None
    action_raw, inbox_item_id, token = parts
    try:
        action = ControlAction(action_raw)
    except ValueError:
        return None
    return ParsedButton(action=action, inbox_item_id=inbox_item_id, token=token)


@dataclass(frozen=True, slots=True)
class ReplyResult:
    """Outcome of handling an inbound control-chat reply."""

    applied: bool
    action: ControlAction | None
    inbox_item_id: str | None
    reason: str | None = None


def handle_reply(
    button_id: str,
    sender_phone_e164: str,
    control_chat_phone_e164: str,
    secret: str,
    *,
    now: int | None = None,
) -> ReplyResult:
    """Verify sender + token and resolve the action to apply (addendum §10)."""
    if sender_phone_e164 != control_chat_phone_e164:
        return ReplyResult(False, None, None, reason="unbound_sender")

    parsed = parse_button_id(button_id)
    if parsed is None:
        return ReplyResult(False, None, None, reason="malformed_button")

    verified_item = verify_token(parsed.token, secret, now=now)
    if verified_item is None or verified_item != parsed.inbox_item_id:
        return ReplyResult(
            False, parsed.action, parsed.inbox_item_id, reason="invalid_or_expired_token"
        )

    return ReplyResult(True, parsed.action, parsed.inbox_item_id)
