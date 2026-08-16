"""Which concrete sender this process replies through (roadmap A6).

The composition root decides what the process is made of, but it may not decide *this* one by
name: ``apps/api/main.py`` is a core file and the boundary test scans it, so naming an adapter
there would put a channel back in the core the moment the loop was closed. The choice lives here
instead, in the package that is allowed to know what WhatsApp is. Connecting a phone line is an
edit to this function and to nothing above it.
"""

from __future__ import annotations

import logging

from apps.api.channels.alerting import WHATSAPP_TEXT as WHATSAPP_ALERT_CHANNEL
from apps.api.channels.alerting import WhatsAppOperatorAlerter
from apps.api.channels.config import ChannelCredentials
from apps.api.channels.sender import ChannelSender
from apps.api.channels.whatsapp import WhatsAppSender
from apps.api.core.alerts import OperatorAlerter

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


def build_alerter(
    sender: ChannelSender | None,
    operator_e164: str | None,
    *,
    declared_channel: str,
    logger: logging.Logger = _logger,
) -> OperatorAlerter | None:
    """How this process reaches a human about an emergency, or ``None`` if it cannot (G3).

    ``None`` is a degraded state and it is announced as one. The orchestrator still detects the
    emergency, still replies to the guest immediately, still files the item for review and still
    writes a CRITICAL log line — what it cannot do is put the alert in front of a person who is
    not watching the logs. That is a deploy without ``CONTROL_CHAT_PHONE_E164`` or without send
    credentials, and it is the single most important warning this process emits.

    The composition root does not name a channel; it passes what it has and takes back a seam,
    which is what keeps ``main.py`` clean under the boundary test.
    """
    if sender is None or operator_e164 is None:
        logger.warning(
            "no emergency alert path configured: an emergency will be detected, answered and "
            "filed, but the only alert will be a log line. Set CONTROL_CHAT_PHONE_E164 and the "
            "send credentials before pointing a real number at this service."
        )
        return None

    if not isinstance(sender, WhatsAppSender):  # pragma: no cover — one sender exists today
        logger.warning("no alerter for %s: emergency alerts will be logged only", type(sender))
        return None

    if declared_channel != WHATSAPP_ALERT_CHANNEL:
        logger.warning(
            "the vocabulary asks for %r on an emergency; this process can only send a WhatsApp "
            "message to %s. Alerts will be delivered and reported as %r.",
            declared_channel,
            operator_e164,
            WHATSAPP_ALERT_CHANNEL,
        )
    return WhatsAppOperatorAlerter(sender, operator_e164)
