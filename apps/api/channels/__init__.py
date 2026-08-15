"""Channel adapters. Everything that knows what WhatsApp or a phone line is lives here.

The core composes an ``OutboundAction``; an adapter renders it into what its channel can
actually carry, and sends it. Per-channel limits — WhatsApp's three quick-reply buttons, a voice
line having no buttons at all — are enforced at that boundary and nowhere else, and as of roadmap
A6 so are the credentials a channel needs (``channels/config.py``).
"""

from apps.api.channels.config import ChannelCredentials
from apps.api.channels.sender import ChannelSender
from apps.api.channels.whatsapp import (
    ChannelSendError,
    ConfigError,
    MetaSettings,
    SendCredentials,
    WhatsAppSender,
)

__all__ = [
    "ChannelCredentials",
    "ChannelSendError",
    "ChannelSender",
    "ConfigError",
    "MetaSettings",
    "SendCredentials",
    "WhatsAppSender",
]
