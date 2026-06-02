"""FastAPI routes for the Meta webhook (addendum §5).

* ``GET  /webhook`` — Meta's subscription handshake: echo ``hub.challenge`` when the token matches.
* ``POST /webhook`` — verify the HMAC, parse, persist-before-enqueue via the ingestion service, and
  return 200 quickly (before classification) so Meta doesn't retry on slow LLM calls.

Tenant resolution (``phone_number_id`` → tenant) is injected as a callable; the real DB-backed
resolver lands with the multi-tenancy slice. Route logic does not depend on persistence/queueing.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from apps.api.core.config import MetaSettings
from apps.api.ingestion.parser import iter_change_values, parse_value
from apps.api.ingestion.security import SIGNATURE_HEADER, verify_signature
from apps.api.ingestion.service import IngestionService

# phone_number_id (or None) → tenant id. Default resolver below is single-tenant/dev.
TenantResolver = Callable[[str | None], str]


def build_router(
    settings: MetaSettings,
    service: IngestionService,
    resolve_tenant: TenantResolver,
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
        # makes the retry safe).
        for phone_number_id, value in iter_change_values(payload):
            tenant_id = resolve_tenant(phone_number_id)
            service.ingest(tenant_id, parse_value(value))
        return Response(status_code=200)

    return router
