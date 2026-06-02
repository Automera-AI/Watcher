"""HMAC verification of Meta webhook payloads (addendum §5).

Meta signs each POST body with the app secret and sends it as ``X-Hub-Signature-256: sha256=<hex>``.
We recompute and compare in constant time; a mismatch rejects the request before any parsing.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
_PREFIX = "sha256="


def expected_signature(app_secret: str, payload: bytes) -> str:
    """Return the ``sha256=<hex>`` signature for ``payload`` under ``app_secret``."""
    digest = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def verify_signature(app_secret: str, payload: bytes, header_value: str | None) -> bool:
    """Constant-time check of the ``X-Hub-Signature-256`` header against ``payload``.

    Returns ``False`` for a missing/malformed header rather than raising, so the caller can
    respond with a single 403 path.
    """
    if not header_value or not header_value.startswith(_PREFIX):
        return False
    return hmac.compare_digest(expected_signature(app_secret, payload), header_value)
