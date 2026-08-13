"""Channel adapters. Everything that knows what WhatsApp or a phone line is lives here.

The core composes an ``OutboundAction``; an adapter renders it into what its channel can
actually carry. Per-channel limits — WhatsApp's three quick-reply buttons, a voice line having
no buttons at all — are enforced at that boundary and nowhere else.
"""

from apps.api.channels.whatsapp import ConfigError, MetaSettings

__all__ = ["ConfigError", "MetaSettings"]
