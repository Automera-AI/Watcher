"""Which tenant owns the endpoint a message arrived at (``TenantResolver``, addendum §3, §5).

The webhook receives an inbound payload addressed to one of our numbers and has to decide whose
data it becomes before anything is written. That decision is the first line of tenant isolation —
get it wrong and a guest's message lands in another customer's inbox — so it is a lookup against
configuration we recorded, never a default, never an inference from the payload's contents.

``channel_configs`` is the table that already holds it: one row per configured endpoint, carrying
the channel's own identifier for it (``external_id``) and the tenant it belongs to. The lookup
deliberately does not filter by channel kind. The question being asked is "who owns this endpoint",
and the answer is the same question for a phone line as for a chat number — a resolver that had to
be told which kind to expect would need editing on the day a second channel is connected, which is
exactly the coupling ``channel_configs`` exists to avoid.
"""

from __future__ import annotations

from sqlalchemy import select

from apps.api.db.engine import SessionScope
from apps.api.db.models import ChannelConfig


class UnknownEndpoint(LookupError):
    """No enabled configuration matches the endpoint a message arrived at."""


class ChannelConfigTenantResolver:
    """Endpoint identifier → tenant id, via ``channel_configs``."""

    def __init__(self, scope: SessionScope) -> None:
        self._scope = scope

    def __call__(self, external_id: str | None) -> str:
        """Resolve, or raise :class:`UnknownEndpoint`.

        Raising is deliberate. The alternatives are worse in both directions: guessing a tenant
        risks writing one customer's message into another's account, and returning quietly loses
        a real message from a real guest to a configuration mistake nobody would ever see. An
        unresolved endpoint surfaces as a 500, the sending platform retries, and the retry
        succeeds once the row exists — which is the behaviour a missing row deserves.
        """
        if external_id is None:
            raise UnknownEndpoint(
                "the inbound payload named no endpoint, so it cannot be attributed to a tenant"
            )
        with self._scope() as session:
            tenant_id = session.execute(
                select(ChannelConfig.tenant_id).where(
                    ChannelConfig.external_id == external_id,
                    ChannelConfig.enabled.is_(True),
                )
            ).scalar_one_or_none()
        if tenant_id is None:
            raise UnknownEndpoint(
                f"no enabled channel_configs row for endpoint {external_id!r}; "
                "add one for the tenant that owns this endpoint"
            )
        return str(tenant_id)
