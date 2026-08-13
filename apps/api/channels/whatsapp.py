"""WhatsApp adapter. This is where WhatsApp's limits are allowed to be true.

The quick-reply cap lives here rather than in ``schemas/envelope.py``, which is roadmap trap #2
resolved: three buttons is a fact about WhatsApp's interactive-message API, not about what a
receptionist can say. Putting it in the core would have capped the voice and web channels at a
number that means nothing to them.

The core is free to compose as many options as it likes. Rendering is where reality applies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from apps.api.schemas.envelope import OutboundAction


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


#: WhatsApp interactive messages carry at most three quick-reply buttons.
QUICK_REPLY_LIMIT = 3


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """What actually goes on the wire to Meta."""

    text: str
    buttons: tuple[str, ...] = ()
    truncated: bool = False


def render(action: OutboundAction) -> RenderedMessage:
    """Fit an action into what WhatsApp can show.

    Truncates rather than raising. The scaffold raised a ``ValueError`` from the core, which
    turns "this channel cannot show six options" into "the receptionist crashed" — and it did so
    at compose time, before anyone knew which channel the reply was even going to.

    ``truncated`` is returned rather than swallowed so a caller that cares — a composer deciding
    whether to spell the remaining options out in the text — can see it happened.
    """
    options = tuple(action.quick_replies or ())
    return RenderedMessage(
        text=action.text,
        buttons=options[:QUICK_REPLY_LIMIT],
        truncated=len(options) > QUICK_REPLY_LIMIT,
    )
