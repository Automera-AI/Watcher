"""FastAPI application factory.

``create_app`` takes its collaborators so tests (and, later, production wiring) inject the
repository, queue, and tenant resolver. The Postgres repository and real queue land in later slices;
until then there is no module-level ``app`` to avoid shipping half-wired globals.
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.api.core.config import MetaSettings
from apps.api.ingestion.ports import ClassificationQueue, MessageRepository
from apps.api.ingestion.router import TenantResolver, build_router
from apps.api.ingestion.service import IngestionService


def create_app(
    settings: MetaSettings,
    repository: MessageRepository,
    queue: ClassificationQueue,
    resolve_tenant: TenantResolver,
) -> FastAPI:
    app = FastAPI(title="Watcher API", version="0.0.0")
    service = IngestionService(repository, queue)
    app.include_router(build_router(settings, service, resolve_tenant))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
