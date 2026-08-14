"""Canonical Pydantic schemas for the open property-system contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PropertyFact(BaseModel):
    """A stable property fact safe for an unverified guest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    updated_at: datetime


class PropertyFacts(BaseModel):
    """Stable facts which may be cached by the caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    property_id: str = Field(min_length=1, max_length=128)
    facts: list[PropertyFact]


class AvailabilityQuery(BaseModel):
    """A live availability request; tenant identity is deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    property_id: str = Field(min_length=1, max_length=128)
    check_in: date
    check_out: date
    guests: int = Field(ge=1, le=100)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> AvailabilityQuery:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class AvailabilityResult(BaseModel):
    """Fresh response from the property system."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    property_id: str
    available: bool
    checked_at: datetime
    total_price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    rate_plan: str | None = None


class Reservation(BaseModel):
    """Reservation data protected by the API identity gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: str = Field(min_length=1, max_length=128)
    property_id: str = Field(min_length=1, max_length=128)
    guest_name: str = Field(min_length=1, max_length=255)
    check_in: date
    check_out: date
    status: Literal["pending", "confirmed", "cancelled", "completed"]

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> Reservation:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class PropertyApiPrincipal(BaseModel):
    """Server-resolved authorization context for one property API credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    identity_verified: bool = False
