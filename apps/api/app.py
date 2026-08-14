"""FastAPI application factory.

``create_app`` takes its collaborators so tests (and, later, production wiring) inject the
repository, queue, and tenant resolver. The Postgres repository and real queue land in later slices;
until then there is no module-level ``app`` to avoid shipping half-wired globals.
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.api.channels import MetaSettings
from apps.api.ingestion.ports import ClassificationQueue, MessageRepository
from apps.api.ingestion.router import TenantResolver, build_router
from apps.api.ingestion.service import IngestionService
from apps.api.property_system import (
    ApiKeyTenantResolver,
    PropertySystemPort,
    build_property_system_router,
)


def create_app(
    settings: MetaSettings,
    repository: MessageRepository,
    queue: ClassificationQueue,
    resolve_tenant: TenantResolver,
    property_system: PropertySystemPort | None = None,
    resolve_api_key: ApiKeyTenantResolver | None = None,
) -> FastAPI:
    if (property_system is None) != (resolve_api_key is None):
        raise ValueError("property_system and resolve_api_key must be configured together")

    app = FastAPI(
        title="Watcher API",
        summary="Channel-neutral receptionist integration API",
        description=(
            "FastAPI implementation of Watcher's OpenAPI contract. Integrations use the stable "
            "v1 HTTP operations and do not depend on a channel- or property-vendor SDK."
        ),
        version="1.0.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    service = IngestionService(repository, queue)
    app.include_router(build_router(settings, service, resolve_tenant))
    if property_system is not None and resolve_api_key is not None:
        app.include_router(build_property_system_router(property_system, resolve_api_key))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
