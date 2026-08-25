"""Does the endpoint this process sends from have an owner it can receive on? (§3, §5)

Receiving and sending read two different sources for the same fact. An inbound delivery is
attributed by looking its endpoint up in ``channel_configs``; an outbound reply goes out through
the endpoint named in this process's credentials. Nothing forces those to agree, and when they
disagree the failure is silent in the worst way — the service boots clean, answers ``/health``,
holds valid credentials, and drops every message that arrives, because the number it is listening
as belongs to nobody.

That is a configuration mistake with no natural moment of discovery: the first person to find it is
a guest whose message went unanswered. This check moves the discovery to startup, where it costs
one log line, and it is a warning rather than a refusal for the same reason the missing sender and
missing alerter are — a process with an unclaimed endpoint still serves the endpoints that *are*
claimed, and a multi-tenant deploy legitimately receives on numbers this process does not send from.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from apps.api.ingestion.ports import UnknownEndpoint

_logger = logging.getLogger(__name__)


def warn_on_unclaimed_endpoints(
    resolve_tenant: Callable[[str | None], str],
    endpoints: Iterable[str],
    *,
    logger: logging.Logger = _logger,
) -> None:
    """Log a warning for each endpoint this process sends from that no tenant claims."""
    for endpoint in endpoints:
        try:
            resolve_tenant(endpoint)
        except UnknownEndpoint:
            logger.warning(
                "endpoint %r has no enabled channel_configs row: this process can send from it "
                "but every message arriving on it will be dropped unattributed. Claim it with "
                "INSERT INTO channel_configs (id, tenant_id, kind, external_id, config, enabled, "
                "created_at, updated_at) VALUES (gen_random_uuid(), '<tenant-uuid>', 'whatsapp', "
                "%r, '{}', true, now(), now());",
                endpoint,
                endpoint,
            )
        except Exception:  # noqa: BLE001 — a database that is not up yet is not a config error
            # Deliberately not fatal and deliberately not specific. This is a diagnostic, and a
            # diagnostic that can stop a deploy is worse than the mistake it looks for.
            logger.warning(
                "could not check whether endpoint %r is configured; the database was unreachable "
                "at startup",
                endpoint,
                exc_info=True,
            )
            return
