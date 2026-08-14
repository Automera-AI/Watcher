"""FastAPI/OpenAPI projection of the vendor-neutral property-system port."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from apps.api.property_system.ports import PropertySystemPort, PropertySystemUnavailable
from apps.api.property_system.schemas import (
    AvailabilityQuery,
    AvailabilityResult,
    PropertyApiPrincipal,
    PropertyFacts,
    Reservation,
)

ApiKeyTenantResolver = Callable[[str], PropertyApiPrincipal | None]
API_KEY_HEADER = APIKeyHeader(
    name="X-API-Key",
    scheme_name="PropertySystemApiKey",
    description="Tenant-scoped Watcher integration credential.",
    auto_error=False,
)


def build_property_system_router(
    port: PropertySystemPort, resolve_api_key: ApiKeyTenantResolver
) -> APIRouter:
    """Build the public contract with deployment-supplied auth and integration."""
    router = APIRouter(prefix="/v1/property-system", tags=["property-system"])

    def principal(
        x_api_key: Annotated[str | None, Security(API_KEY_HEADER)],
    ) -> PropertyApiPrincipal:
        resolved = resolve_api_key(x_api_key or "")
        if resolved is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
        return resolved

    @router.get(
        "/properties/{property_id}/facts",
        response_model=PropertyFacts,
        operation_id="getPropertyFacts",
        summary="Get stable property facts",
        responses={
            401: {"description": "Invalid API key"},
            404: {"description": "Not found"},
            503: {"description": "Property system unavailable"},
        },
    )
    async def facts(
        property_id: str,
        auth: Annotated[PropertyApiPrincipal, Depends(principal)],
    ) -> PropertyFacts:
        try:
            result = await port.get_property_facts(auth.tenant_id, property_id)
        except PropertySystemUnavailable as error:
            raise HTTPException(status_code=503, detail="property system unavailable") from error
        if result is None:
            raise HTTPException(status_code=404, detail="property not found")
        return result

    @router.post(
        "/availability/check",
        response_model=AvailabilityResult,
        operation_id="checkPropertyAvailability",
        summary="Check live property availability",
        responses={
            401: {"description": "Invalid API key"},
            503: {"description": "Property system unavailable"},
        },
    )
    async def availability(
        query: AvailabilityQuery,
        auth: Annotated[PropertyApiPrincipal, Depends(principal)],
    ) -> AvailabilityResult:
        try:
            return await port.check_availability(auth.tenant_id, query)
        except PropertySystemUnavailable as error:
            raise HTTPException(status_code=503, detail="property system unavailable") from error

    @router.get(
        "/reservations/{reference}",
        response_model=Reservation,
        operation_id="getReservation",
        summary="Get an identity-protected reservation",
        responses={
            401: {"description": "Invalid API key"},
            403: {"description": "Verified identity required"},
            404: {"description": "Not found"},
            503: {"description": "Property system unavailable"},
        },
    )
    async def reservation(
        reference: str,
        auth: Annotated[PropertyApiPrincipal, Depends(principal)],
    ) -> Reservation:
        if not auth.identity_verified:
            raise HTTPException(status_code=403, detail="verified identity required")
        try:
            result = await port.get_reservation(auth.tenant_id, reference)
        except PropertySystemUnavailable as error:
            raise HTTPException(status_code=503, detail="property system unavailable") from error
        if result is None:
            raise HTTPException(status_code=404, detail="reservation not found")
        return result

    return router
