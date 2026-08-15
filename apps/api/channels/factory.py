"""Which concrete sender this process replies through (roadmap A6).

The composition root decides what the process is made of, but it may not decide *this* one by
name: ``apps/api/main.py`` is a core file and the boundary test scans it, so naming an adapter
there would put a channel back in the core the moment the loop was closed. The choice lives here
instead, in the package that is allowed to know what WhatsApp is. Connecting a phone line is an
edit to this function and to nothing above it.
"""

from __future__ import annotations

import logging

from apps.api.channels.config import ChannelCredentials
from apps.api.channels.sender import ChannelSender
from apps.api.channels.whatsapp import WhatsAppSender

_logger = logging.getLogger(__name__)


def build_sender(
    credentials: ChannelCredentials, *, logger: logging.Logger = _logger
) -> ChannelSender | None:
    """The outbound sender, or ``None`` when this process has no credentials to send with.

    ``None`` rather than an exception because a process that cannot reply is a real state and a
    working one: it still ingests, classifies, continues conversations and records the replies it
    composed. That is every deploy between B1 and B4, and it deserves one loud line at startup
    rather than a refusal to boot.
    """
    if not credentials.can_send():
        logger.warning(
            "no send credentials configured: replies will be composed and recorded but not "
            "delivered. Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID to close the loop."
        )
        return None
    return WhatsAppSender(credentials.send_credentials())
