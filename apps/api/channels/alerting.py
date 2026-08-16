"""Delivering an emergency alert on the channel this process actually has (roadmap G3).

``core/alerts.py`` defines what an alert *is*; this is the half that knows a WhatsApp number is
the only way out of this process today. It lives in ``channels/`` for the same reason the sender
does — the boundary test scans everything outside this package, and an alerter that names a
channel in the core is a core file that needs editing on the day a phone line is connected.

**What this is not.** It is not ``phone_call_to_operator``. It is a WhatsApp message to the
operator's number, which is a notification a person can miss and a call is not. The gap is
reported rather than smoothed over: :meth:`alert` returns the channel it used, ``build_alerter``
warns once at startup, and the voice alert stays on the board as its own item.
"""

from __future__ import annotations

import logging

from apps.api.channels.whatsapp import WhatsAppSender
from apps.api.core.alerts import AlertOutcome, EmergencyAlert
from apps.api.schemas.envelope import OutboundAction

_logger = logging.getLogger(__name__)

#: What :class:`WhatsAppOperatorAlerter` reports having delivered on. Never equal to the
#: vocabulary's ``phone_call_to_operator``, which is the point — see the module docstring.
WHATSAPP_TEXT = "whatsapp_text"


class WhatsAppOperatorAlerter:
    """Messages the operator's own number about an emergency (``OperatorAlerter``).

    Reuses the process's one :class:`WhatsAppSender`, so the alert goes out over the connection
    pool that is already warm and inherits its retries — a resent alert is far better than a lost
    one, and the sender's backoff is bounded at a few seconds.
    """

    def __init__(
        self,
        sender: WhatsAppSender,
        operator_e164: str,
        *,
        logger: logging.Logger = _logger,
    ) -> None:
        self._sender = sender
        self._operator = operator_e164
        self._logger = logger

    async def alert(self, alert: EmergencyAlert) -> AlertOutcome:
        """Put the alert on the wire. Returns the outcome; never raises.

        A raise here would travel back up into the message path and could cost the guest the
        immediate reply, which is the one thing the vocabulary says must happen. The failure is
        logged at CRITICAL — an undelivered emergency alert is the most serious line this process
        can write — and reported in the outcome for the inbox item.
        """
        action = OutboundAction(kind="say", text=alert.summary())
        try:
            await self._sender.send_to(action, self._operator)
        except Exception as exc:  # noqa: BLE001 — an alert failure must not raise into the path
            detail = f"{type(exc).__name__}: {exc}"
            self._logger.critical(
                "EMERGENCY ALERT UNDELIVERED: trigger=%s guest=%s message=%s — %s",
                alert.trigger_id,
                alert.guest_identity,
                alert.message_id,
                detail,
            )
            return AlertOutcome(delivered=False, channel=WHATSAPP_TEXT, detail=detail)
        return AlertOutcome(delivered=True, channel=WHATSAPP_TEXT)
