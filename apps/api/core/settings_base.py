"""The two rules every settings object in this tree follows, and the error it raises (roadmap A6).

Extracted from ``core/config.py`` when the channel credential fields moved behind ``channels/``.
Both halves of the configuration — the core's and the adapter's — need the same placeholder
handling and the same "name everything that is missing at once" error, and neither can import the
other without a cycle. So the shared part lives here, where it names no channel and no subsystem.

``ConfigError`` moved here too. It had been living inside a channel adapter since before there was
a configuration layer, which meant the core imported its own missing-configuration error from one.
``apps.api.channels`` still re-exports it, because that is where callers and tests have always
imported it from and moving every import site is not what A6 is for.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing."""


def is_placeholder(value: str) -> bool:
    """``<ANTHROPIC_API_KEY>`` from ``.env.example``, or an assignment left empty."""
    stripped = value.strip()
    return not stripped or (stripped.startswith("<") and stripped.endswith(">"))


class PlaceholderAwareSettings(BaseSettings):
    """Settings for which an unedited ``.env.example`` line means "not configured"."""

    @model_validator(mode="before")
    @classmethod
    def _drop_placeholders(cls, data: Any) -> Any:
        """Treat ``<LIKE_THIS>`` and the empty string as "not configured".

        ``.env.example`` is meant to be copied and filled in, so the failure mode this guards is
        a real one: an unedited line reaching a provider as a literal API key, where it surfaces
        as an authentication error from a third party instead of a missing-variable error from
        us. An empty assignment (``FOO=``) is the same statement made a different way.

        The placeholder is *removed* rather than replaced with ``None`` so that a field with a
        default gets its default. ``.env.example`` ships ``META_GRAPH_API_VERSION=<...>``;
        copying that file unedited should leave the pinned default in place, not fail
        validation on a field that has a perfectly good answer already.
        """
        if not isinstance(data, dict):
            return data
        return {
            key: value
            for key, value in data.items()
            if not (isinstance(value, str) and is_placeholder(value))
        }

    @staticmethod
    def _require(**values: str | SecretStr | None) -> tuple[str, ...]:
        """Unwrap the given settings, or raise naming *every* missing one in a single message.

        One variable per error means one restart per variable. A first deploy is typically
        missing three or four at once, so they are collected and reported together.
        """
        missing = sorted(name for name, value in values.items() if value is None)
        if missing:
            raise ConfigError(
                "Missing required environment variable"
                f"{'s' if len(missing) > 1 else ''}: {', '.join(missing)}. "
                "See .env.example."
            )
        return tuple(
            value.get_secret_value() if isinstance(value, SecretStr) else str(value)
            for value in values.values()
        )
