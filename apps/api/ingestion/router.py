"""FastAPI routes for the Meta webhook (addendum §5).

* ``GET  /webhook`` — Meta's subscription handshake: echo ``hub.challenge`` when the token matches.
* ``POST /webhook`` — verify the HMAC, parse, persist-before-enqueue via the ingestion service, and
  return 200 quickly (before classification) so Meta doesn't retry on slow LLM calls.

**What a non-200 means here, and why only one thing earns it.** Meta redelivers anything that isn't
a 200 and disables the subscription outright if the failures persist — so the status code is a
retry instruction, and a retry is only ever the right answer to a *transient* fault. A persist or
enqueue that fails is exactly that (the database blinked; the next delivery lands), so it still
propagates and still 500s, and idempotency on ``external_id`` makes the redelivery safe. An
endpoint no ``channel_configs`` row claims is the opposite: no number of redeliveries writes the
missing row, and answering 500 to it would let one unconfigured number take the callback down for
every tenant that shares it. That case is acknowledged and logged loudly instead — see ``receive``.

Tenant resolution (``phone_number_id`` → tenant) is injected as a callable; the real DB-backed
resolver lands with the multi-tenancy slice. Route logic does not depend on persistence/queueing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from apps.api.channels.whatsapp import MetaSettings
from apps.api.ingestion.parser import iter_change_values, parse_value
from apps.api.ingestion.ports import UnclaimedDeliveryStore, UnknownEndpoint
from apps.api.ingestion.security import SIGNATURE_HEADER, verify_signature
from apps.api.ingestion.service import IngestionService
from apps.api.schemas.message import MessageEnvelope

_logger = logging.getLogger(__name__)

# phone_number_id (or None) → tenant id. Default resolver below is single-tenant/dev.
TenantResolver = Callable[[str | None], str]


def build_router(
    settings: MetaSettings,
    service: IngestionService,
    resolve_tenant: TenantResolver,
    unclaimed_deliveries: UnclaimedDeliveryStore,
) -> APIRouter:
    router = APIRouter()

    @router.get("/webhook")
    def verify(
        mode: str | None = Query(default=None, alias="hub.mode"),
        token: str | None = Query(default=None, alias="hub.verify_token"),
        challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> Response:
        if mode == "subscribe" and token == settings.webhook_verify_token and challenge is not None:
            return PlainTextResponse(challenge)
        return PlainTextResponse("verification failed", status_code=403)

    @router.post("/webhook")
    async def receive(request: Request) -> Response:
        body = await request.body()
        if not verify_signature(settings.app_secret, body, request.headers.get(SIGNATURE_HEADER)):
            return PlainTextResponse("invalid signature", status_code=403)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return PlainTextResponse("invalid payload", status_code=400)

        # Ingest each change under its resolved tenant; 200 returns after persist+enqueue, not after
        # classification (§5). Enqueue/persist failures surface as 500 so Meta retries (idempotency
        # makes the retry safe) — see the module docstring for why an unresolved endpoint does not.
        for phone_number_id, value in iter_change_values(payload):
            messages = parse_value(value)
            try:
                tenant_id = resolve_tenant(phone_number_id)
            except UnknownEndpoint as exc:
                # This write is intentionally before the 200. If it fails, the exception becomes
                # a 500 and Meta retries; acknowledging is safe only after the complete change has
                # a durable recovery copy.
                unclaimed_deliveries.save(phone_number_id, value, str(exc))
                _log_unconfigured_endpoint(phone_number_id, messages, exc)
                continue
            result = service.ingest(tenant_id, messages)
            if result.accepted or result.duplicates:
                _logger.info(
                    "webhook: endpoint %s → tenant %s, accepted=%d duplicate=%d",
                    phone_number_id,
                    tenant_id,
                    result.accepted,
                    result.duplicates,
                )
        return Response(status_code=200)

    return router


def _log_unconfigured_endpoint(
    phone_number_id: str | None,
    messages: list[MessageEnvelope],
    exc: UnknownEndpoint,
) -> None:
    """Record a delivery we acknowledged but could not attribute, in enough detail to act on.

    The quarantine row is the recovery path; the log carries the information needed to act. The
    endpoint identifier, because it is otherwise unknowable — an unconfigured number has,
    by definition, left no trace anywhere else, and reading it out of a stack trace is how this
    was diagnosed the hard way. The statement that fixes it, because the gap between "I can see the
    id" and "the row exists" is the whole outage. The complete change is stored in
    ``unclaimed_deliveries`` before this warning is emitted; logs are diagnostic, not storage.
    """
    _logger.warning(
        "webhook: dropped %d message(s) for unconfigured endpoint %r (%s). "
        "Nothing is retried — Meta was acknowledged, because a redelivery cannot write the "
        "missing row. To claim this endpoint: INSERT INTO channel_configs "
        "(id, tenant_id, kind, external_id, config, enabled, created_at, updated_at) VALUES "
        "(gen_random_uuid(), '<tenant-uuid>', 'whatsapp', %r, '{}', true, now(), now()); "
        "The complete change was saved to unclaimed_deliveries before acknowledgement.",
        len(messages),
        phone_number_id,
        exc,
        phone_number_id,
    )
