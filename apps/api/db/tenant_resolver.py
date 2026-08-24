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

**Why this one keeps the unstamped session (B2).** Everything else that touches the database takes
a :data:`~apps.api.db.engine.TenantScope` and acts as a named tenant. This cannot: it is the
question asked before the answer exists. Migration ``004`` therefore gives ``channel_configs`` a
second policy that permits a *read* by a session with no tenant set, and only then — a session that
has adopted a tenant sees that tenant's endpoints and no others, and a session with no tenant sees
endpoint rows and nothing else in the database, because every other policy fails closed on an
absent setting. The blast radius of the exception is one column of routing configuration.
"""

from __future__ import annotations

from sqlalchemy import select

from apps.api.db.engine import SessionScope
from apps.api.db.models import ChannelConfig
from apps.api.ingestion.ports import UnknownEndpoint

__all__ = ["ChannelConfigTenantResolver", "UnknownEndpoint"]


class ChannelConfigTenantResolver:
    """Endpoint identifier → tenant id, via ``channel_configs``."""

    def __init__(self, scope: SessionScope) -> None:
        self._scope = scope

    def __call__(self, external_id: str | None) -> str:
        """Resolve, or raise :class:`~apps.api.ingestion.ports.UnknownEndpoint`.

        Raising rather than guessing is deliberate: inventing a tenant for an endpoint nobody
        configured risks writing one customer's message into another's account, which is the exact
        failure this lookup exists to prevent. What raising must *not* do is return quietly — a
        real message from a real guest lost to a configuration mistake nobody ever sees is the
        other bad outcome, so the caller is required to make the miss loud.

        This used to argue that the miss should surface as a 500 so the sending platform retries
        until the row exists. That was wrong about the platform. Meta redelivers over minutes, not
        the hours or days it takes an operator to notice and write a row, and sustained non-200s
        get the webhook subscription *disabled* — turning one endpoint's missing config into an
        outage for every tenant sharing the callback. The route therefore acknowledges the delivery
        and logs the endpoint plus the payload it dropped; see ``ingestion/router.py``.
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
