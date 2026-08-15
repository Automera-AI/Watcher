"""WhatsApp adapter. This is where WhatsApp's limits are allowed to be true.

The quick-reply cap lives here rather than in ``schemas/envelope.py``, which is roadmap trap #2
resolved: three buttons is a fact about WhatsApp's interactive-message API, not about what a
receptionist can say. Putting it in the core would have capped the voice and web channels at a
number that means nothing to them.

The core is free to compose as many options as it likes. Rendering is where reality applies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from apps.api.core.settings_base import ConfigError
from apps.api.schemas.envelope import InboundTurn, OutboundAction

__all__ = [
    "GRAPH_API_HOST",
    "QUICK_REPLY_LIMIT",
    "ChannelSendError",
    "ConfigError",
    "MetaSettings",
    "RenderedMessage",
    "SendCredentials",
    "WhatsAppSender",
    "render",
    "to_payload",
]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MetaSettings:
    """Meta WhatsApp Cloud API webhook settings (addendum §5)."""

    app_secret: str
    """Verifies the X-Hub-Signature-256 HMAC on every inbound POST."""

    webhook_verify_token: str
    """Echoed back during Meta's GET subscription handshake."""

    # Built by `ChannelCredentials.meta()` (apps/api/channels/config.py), which is the only thing
    # that reads the environment. The `from_env` classmethod that used to live here was the last
    # hand-rolled os.environ lookup in the tree, and it bypassed the placeholder handling A1 added
    # — two ways to read the same two variables, disagreeing about what `<META_APP_SECRET>` means.


@dataclass(frozen=True, slots=True)
class SendCredentials:
    """What this adapter needs to put a message on the wire: who to send as, and with what.

    Declared beside :class:`MetaSettings` rather than in ``channels/config.py`` so that the
    settings module can import the adapter and not the other way round — the adapter is the thing
    that knows what a send needs; configuration is only where the values come from.
    """

    access_token: str
    phone_number_id: str
    graph_api_version: str


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


#: WhatsApp truncates a quick-reply title past this; doing it here means the button says something
#: rather than being silently cut mid-word by the platform.
BUTTON_TITLE_LIMIT = 20

#: Meta's Graph API host. A constructor argument in the sender so a test never resolves it.
GRAPH_API_HOST = "https://graph.facebook.com"

#: Statuses worth another attempt: rate limits and Meta having a bad minute. A 400 or a 401 is a
#: statement about the request — a revoked token, a number that cannot be messaged — and retrying
#: it only delays the same failure.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

_BACKOFF_BASE_SECONDS = 0.5
_MAX_BACKOFF_SECONDS = 4.0


class ChannelSendError(RuntimeError):
    """A reply could not be delivered to the channel."""


def to_payload(action: OutboundAction, recipient: str) -> dict[str, Any]:
    """The Cloud API message body for one action.

    Quick replies become an interactive button message and everything else becomes plain text,
    which is the only shape distinction WhatsApp actually makes here. The three-button cap and the
    title length are applied on the way out: this is the boundary where the channel's limits are
    true, and the core is free to have composed six options with long names.
    """
    rendered = render(action)
    if not rendered.buttons:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": rendered.text},
        }
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": rendered.text},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": f"qr_{index}", "title": title[:BUTTON_TITLE_LIMIT]},
                    }
                    for index, title in enumerate(rendered.buttons)
                ]
            },
        },
    }


class WhatsAppSender:
    """Delivers an :class:`OutboundAction` to the Cloud API (``ChannelSender``, roadmap A6).

    Until this existed the receptionist composed replies that went nowhere: ``ChannelSender`` was a
    protocol with no implementation, so a guest who messaged the number got a perfectly filed
    silence. This is the other end of the wire the webhook arrives on.

    **The client is synchronous and the send is not.** ``httpx.Client`` is thread-safe and lives
    for the life of the process, so connections are reused across messages; the blocking call is
    handed to a worker thread rather than made on the caller's event loop. That matters because the
    loop this runs on is not always ours — under ``BackgroundTasksQueue`` it is the server's, and a
    blocking POST there would stall every request in flight, not just this reply.
    """

    def __init__(
        self,
        credentials: SendCredentials,
        *,
        client: httpx.Client | None = None,
        host: str = GRAPH_API_HOST,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger = _logger,
    ) -> None:
        self._credentials = credentials
        self._client = client or httpx.Client()
        self._host = host.rstrip("/")
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._logger = logger

    @property
    def url(self) -> str:
        """The versioned messages endpoint for the number we send as."""
        return (
            f"{self._host}/{self._credentials.graph_api_version}"
            f"/{self._credentials.phone_number_id}/messages"
        )

    async def send(self, action: OutboundAction, turn: InboundTurn) -> None:
        """Send one reply, or raise :class:`ChannelSendError`."""
        payload = to_payload(action, turn.channel_identity)
        await asyncio.to_thread(self._post, payload)

    def _post(self, payload: dict[str, Any]) -> None:
        headers = {
            "Authorization": f"Bearer {self._credentials.access_token}",
            "Content-Type": "application/json",
        }
        last_error = "no attempt was made"

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(
                    self.url, headers=headers, json=payload, timeout=self._timeout
                )
            except httpx.HTTPError as exc:  # timeouts, resets, DNS — the network saying come back
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                if response.status_code not in _RETRYABLE_STATUS:
                    raise ChannelSendError(last_error)

            self._logger.warning(
                "send failed (attempt %d/%d): %s", attempt, self._max_attempts, last_error
            )
            if attempt < self._max_attempts:
                self._sleep(min(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), _MAX_BACKOFF_SECONDS))

        raise ChannelSendError(f"undelivered after {self._max_attempts} attempts — {last_error}")

    def close(self) -> None:
        """Release the connection pool. Called from the application's shutdown handler."""
        self._client.close()
