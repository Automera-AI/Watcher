"""The web server. Its only job is to answer Meta within a couple of seconds.

Verify the signature, drop the payload on the queue, return 200. Everything slow happens in a
worker. If this endpoint ever does real work, Meta starts retrying and you get duplicates.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, FastAPI, Request, Response

from app.settings import settings

log = structlog.get_logger()
app = FastAPI(title="Watcher receptionist")
router = APIRouter(prefix="/webhooks")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/whatsapp")
async def whatsapp_verify(request: Request) -> Response:
    """Meta calls this once when you register the webhook."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


@router.post("/whatsapp")
async def whatsapp_receive(request: Request) -> Response:
    body = await request.body()

    from app.channels.whatsapp import WhatsAppAdapter

    adapter = WhatsAppAdapter("", settings.whatsapp_access_token)
    if not await adapter.verify(dict(request.headers), body):
        log.warning("whatsapp_bad_signature")
        return Response(status_code=401)

    # TODO Phase A day 4: push onto the Redis queue and let the worker do the rest.
    log.info("whatsapp_received", bytes=len(body))
    return Response(status_code=200)


app.include_router(router)
