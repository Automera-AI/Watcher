"""Read port implemented once by any property-system integration."""

from __future__ import annotations

from typing import Protocol

from apps.api.property_system.schemas import (
    AvailabilityQuery,
    AvailabilityResult,
    PropertyFacts,
    Reservation,
)


class PropertySystemUnavailable(RuntimeError):
    """The live upstream cannot provide a trustworthy answer."""


class PropertySystemPort(Protocol):
    """Channel- and vendor-neutral property data used by the receptionist."""

    async def get_property_facts(
        self, tenant_id: str, property_id: str
    ) -> PropertyFacts | None: ...

    async def check_availability(
        self, tenant_id: str, query: AvailabilityQuery
    ) -> AvailabilityResult: ...

    async def get_reservation(self, tenant_id: str, reference: str) -> Reservation | None: ...
