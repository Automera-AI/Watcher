"""Runtime configuration, read from the environment (see .env.example).

Kept to the values the ingestion slice actually needs; broader settings land with their slices.
Secrets are per-tenant in production (encrypted at rest, addendum §3) — these env values cover the
single-tenant/dev path and the shared webhook credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing."""


@dataclass(frozen=True, slots=True)
class MetaSettings:
    """Meta WhatsApp Cloud API webhook settings (addendum §5)."""

    app_secret: str
    """Verifies the X-Hub-Signature-256 HMAC on every inbound POST."""

    webhook_verify_token: str
    """Echoed back during Meta's GET subscription handshake."""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> MetaSettings:
        source = os.environ if env is None else env
        try:
            return cls(
                app_secret=source["META_APP_SECRET"],
                webhook_verify_token=source["META_WEBHOOK_VERIFY_TOKEN"],
            )
        except KeyError as exc:
            raise ConfigError(f"Missing required environment variable: {exc.args[0]}") from exc
