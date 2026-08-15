"""WhatsApp adapter. This is where WhatsApp's limits are allowed to be true.

The quick-reply cap lives here rather than in ``schemas/envelope.py``, which is roadmap trap #2
resolved: three buttons is a fact about WhatsApp's interactive-message API, not about what a
receptionist can say. Putting it in the core would have capped the voice and web channels at a
number that means nothing to them.

The core is free to compose as many options as it likes. Rendering is where reality applies.
"""

from __future__ import annotations

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

    # Built by `Settings.meta()` (apps/api/core/config.py), which is the only thing that reads the
    # environment. The `from_env` classmethod that used to live here was the last hand-rolled
    # os.environ lookup in the tree, and it bypassed the placeholder handling A1 added — two ways
    # to read the same two variables, disagreeing about what `<META_APP_SECRET>` means.


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
