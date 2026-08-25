from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api.app import create_app
from apps.api.channels import MetaSettings
from apps.api.property_system import (
    AvailabilityQuery,
    AvailabilityResult,
    PropertyApiPrincipal,
    PropertyFacts,
    PropertySystemUnavailable,
    Reservation,
)
from apps.api.tests.fakes import InMemoryRepository, RecordingQueue, RecordingUnclaimedDeliveryStore


class FakePropertySystem:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.unavailable = False

    async def get_property_facts(self, tenant_id: str, property_id: str) -> PropertyFacts | None:
        self.calls.append((tenant_id, "facts"))
        if property_id == "missing":
            return None
        return PropertyFacts(property_id=property_id, facts=[])

    async def check_availability(
        self, tenant_id: str, query: AvailabilityQuery
    ) -> AvailabilityResult:
        self.calls.append((tenant_id, "availability"))
        if self.unavailable:
            raise PropertySystemUnavailable
        return AvailabilityResult(
            property_id=query.property_id,
            available=True,
            checked_at=datetime.now(UTC),
            total_price=Decimal("250.00"),
            currency=query.currency,
        )

    async def get_reservation(self, tenant_id: str, reference: str) -> Reservation | None:
        self.calls.append((tenant_id, "reservation"))
        return Reservation(
            reference=reference,
            property_id="villa-1",
            guest_name="Test Guest",
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 3),
            status="confirmed",
        )


def make_client(fake: FakePropertySystem) -> TestClient:
    def resolve_tenant(value: str | None) -> str:
        return value or "default"

    app = create_app(
        MetaSettings(app_secret="secret", webhook_verify_token="token"),
        InMemoryRepository(),
        RecordingQueue(),
        resolve_tenant,
        RecordingUnclaimedDeliveryStore(),
        property_system=fake,
        resolve_api_key=lambda key: {
            "alpha-key": PropertyApiPrincipal(tenant_id="tenant-alpha"),
            "bravo-key": PropertyApiPrincipal(tenant_id="tenant-bravo"),
            "verified-key": PropertyApiPrincipal(tenant_id="tenant-alpha", identity_verified=True),
        }.get(key),
    )
    return TestClient(app)


def test_openapi_exposes_vendor_neutral_contract() -> None:
    schema = make_client(FakePropertySystem()).get("/openapi.json").json()
    assert "/v1/property-system/availability/check" in schema["paths"]
    assert "AvailabilityQuery" in schema["components"]["schemas"]
    assert schema["info"]["version"] == "1.0.0"
    security = schema["components"]["securitySchemes"]["PropertySystemApiKey"]
    assert security == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Tenant-scoped Watcher integration credential.",
    }
    availability = schema["paths"]["/v1/property-system/availability/check"]["post"]
    assert availability["operationId"] == "checkPropertyAvailability"
    assert availability["security"] == [{"PropertySystemApiKey": []}]


def test_auth_resolves_tenant_outside_payload() -> None:
    fake = FakePropertySystem()
    response = make_client(fake).get(
        "/v1/property-system/properties/villa-1/facts", headers={"X-API-Key": "alpha-key"}
    )
    assert response.status_code == 200
    assert fake.calls == [("tenant-alpha", "facts")]

    response = make_client(fake).get(
        "/v1/property-system/properties/villa-1/facts", headers={"X-API-Key": "bravo-key"}
    )
    assert response.status_code == 200
    assert fake.calls[-1] == ("tenant-bravo", "facts")


def test_invalid_key_never_calls_port() -> None:
    fake = FakePropertySystem()
    response = make_client(fake).get("/v1/property-system/properties/villa-1/facts")
    assert response.status_code == 401
    assert fake.calls == []


def test_availability_is_live_on_every_request_and_failure_is_safe() -> None:
    fake = FakePropertySystem()
    client = make_client(fake)
    payload = {
        "property_id": "villa-1",
        "check_in": "2026-09-01",
        "check_out": "2026-09-03",
        "guests": 2,
    }
    headers = {"X-API-Key": "alpha-key"}
    assert (
        client.post(
            "/v1/property-system/availability/check", json=payload, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/property-system/availability/check", json=payload, headers=headers
        ).status_code
        == 200
    )
    assert fake.calls.count(("tenant-alpha", "availability")) == 2
    fake.unavailable = True
    assert (
        client.post(
            "/v1/property-system/availability/check", json=payload, headers=headers
        ).status_code
        == 503
    )


def test_reservation_requires_verified_identity_before_port_call() -> None:
    fake = FakePropertySystem()
    client = make_client(fake)
    headers = {"X-API-Key": "alpha-key"}
    assert client.get("/v1/property-system/reservations/ABC", headers=headers).status_code == 403
    assert fake.calls == []
    headers["X-API-Key"] = "verified-key"
    assert client.get("/v1/property-system/reservations/ABC", headers=headers).status_code == 200
    assert fake.calls == [("tenant-alpha", "reservation")]


def test_reservation_rejects_invalid_date_range() -> None:
    with pytest.raises(ValidationError, match="check_out must be after check_in"):
        Reservation(
            reference="ABC",
            property_id="villa-1",
            guest_name="Test Guest",
            check_in=date(2026, 9, 3),
            check_out=date(2026, 9, 1),
            status="confirmed",
        )


def test_property_api_dependencies_must_be_configured_together() -> None:
    def resolve_tenant(value: str | None) -> str:
        return value or "default"

    with pytest.raises(ValueError, match="must be configured together"):
        create_app(
            MetaSettings(app_secret="secret", webhook_verify_token="token"),
            InMemoryRepository(),
            RecordingQueue(),
            resolve_tenant,
            RecordingUnclaimedDeliveryStore(),
            property_system=FakePropertySystem(),
        )
