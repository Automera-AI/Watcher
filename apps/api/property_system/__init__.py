"""Vendor-neutral property-system boundary and OpenAPI projection."""

from apps.api.property_system.ports import PropertySystemPort, PropertySystemUnavailable
from apps.api.property_system.router import ApiKeyTenantResolver, build_property_system_router
from apps.api.property_system.schemas import (
    AvailabilityQuery,
    AvailabilityResult,
    PropertyApiPrincipal,
    PropertyFact,
    PropertyFacts,
    Reservation,
)

__all__ = [
    "ApiKeyTenantResolver",
    "AvailabilityQuery",
    "AvailabilityResult",
    "PropertyFact",
    "PropertyFacts",
    "PropertyApiPrincipal",
    "PropertySystemPort",
    "PropertySystemUnavailable",
    "Reservation",
    "build_property_system_router",
]
