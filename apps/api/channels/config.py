"""Every environment variable that belongs to a channel rather than to the core (roadmap A6).

These fields lived on ``core/config.py`` until A6 and were the last entry in the boundary test's
``KNOWN_LEAKS``: an access token and a phone-number id are facts about WhatsApp, and a core object
that declares them is a core object that will need editing on the day a phone line is connected.
They are declared here, in the adapter package that is allowed to know what WhatsApp is, and the
core inherits them — so ``Settings`` still reads one ``.env`` and one environment, and still exposes
one object, while the *knowledge* of which variables a channel needs sits with the channel.

The accessors are here for the same reason. ``meta()`` answers "can this process verify an inbound
webhook"; ``send_credentials()`` answers "can it reply". Both are checked at the point of use, so a
process that only ingests is not required to hold send credentials it will never spend.
"""

from __future__ import annotations

from pydantic import SecretStr

from apps.api.channels.whatsapp import MetaSettings, SendCredentials
from apps.api.core.settings_base import PlaceholderAwareSettings
from apps.api.schemas.common import PhoneE164


class ChannelCredentials(PlaceholderAwareSettings):
    """The channel half of the process configuration. ``Settings`` extends this."""

    # ── Meta WhatsApp Business Cloud API — ingestion (addendum §5) ──────────────────────────
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_webhook_verify_token: SecretStr | None = None
    meta_graph_api_version: str = "v21.0"

    # ── The watched number and the operator's (addendum §4, §10) ───────────────────────────
    whatsapp_access_token: SecretStr | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_number_e164: PhoneE164 | None = None

    def meta(self) -> MetaSettings:
        """Webhook verification settings, or :class:`ConfigError` naming what is missing."""
        secret, token = self._require(
            META_APP_SECRET=self.meta_app_secret,
            META_WEBHOOK_VERIFY_TOKEN=self.meta_webhook_verify_token,
        )
        return MetaSettings(app_secret=secret, webhook_verify_token=token)

    def send_credentials(self) -> SendCredentials:
        """Credentials for outbound sends, or :class:`ConfigError` naming what is missing."""
        token, phone_number_id = self._require(
            WHATSAPP_ACCESS_TOKEN=self.whatsapp_access_token,
            WHATSAPP_PHONE_NUMBER_ID=self.whatsapp_phone_number_id,
        )
        return SendCredentials(
            access_token=token,
            phone_number_id=phone_number_id,
            graph_api_version=self.meta_graph_api_version,
        )

    def can_send(self) -> bool:
        """Whether replies can go out at all.

        Asked by the composition root so a half-configured deploy still ingests, classifies and
        files rather than refusing to start. A process that cannot reply is a real state — it is
        every deploy between B1 and B4 — and it is worth one loud warning at startup rather than a
        crash, because everything except the last step still works.
        """
        return self.whatsapp_access_token is not None and self.whatsapp_phone_number_id is not None
