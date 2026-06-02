"""Short-lived signed tokens binding a control-chat button to an inbox item (addendum §10).

Each interactive button carries ``inbox_item_id`` + a signed token (HMAC, ~15-min TTL) so an inbound
reply is authenticated and bound to a specific item. Self-contained (no DB lookup): the token holds
the item id and an expiry, signed with the tenant secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

DEFAULT_TTL_SECONDS = 15 * 60


def _sign(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_token(
    inbox_item_id: str,
    secret: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> str:
    """Issue ``<item_id>.<expiry>.<sig>`` valid for ``ttl_seconds``."""
    expiry = (int(time.time()) if now is None else now) + ttl_seconds
    payload = f"{inbox_item_id}.{expiry}"
    return f"{payload}.{_sign(secret, payload)}"


def verify_token(token: str, secret: str, *, now: int | None = None) -> str | None:
    """Return the bound ``inbox_item_id`` if the token is valid and unexpired, else ``None``."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    inbox_item_id, expiry_raw, signature = parts
    payload = f"{inbox_item_id}.{expiry_raw}"
    if not hmac.compare_digest(_sign(secret, payload), signature):
        return None
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return None
    current = int(time.time()) if now is None else now
    if current >= expiry:
        return None
    return inbox_item_id
