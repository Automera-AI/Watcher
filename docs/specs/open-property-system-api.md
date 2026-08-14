# Open property-system API — delivery specification

## Boundary

Watcher integrates with property systems through one vendor-neutral contract. The domain never
imports a Hostaway, Guesty, Cloudbeds, channel, or transport SDK. A deployment supplies a
`PropertySystemPort`; the same port is used by the receptionist and exposed through FastAPI's
OpenAPI document for operations and integration testing.

This slice is intentionally read-only. Booking creation, modification, payment, and access-code
disclosure require separate authority and identity-verification specifications.

## Schemas and operations

All request and response objects are Pydantic v2 models.

* `get_property_facts(tenant_id, property_id)` returns stable, non-sensitive facts.
* `check_availability(tenant_id, query)` performs a live read. Availability is never cached.
* `get_reservation(tenant_id, reference)` returns a reservation only when the server-side
  credential resolver supplies a verified-identity principal.

The REST projection is under `/v1/property-system`. Tenant identity comes from an injected API-key
resolver, never from request JSON or a query parameter. FastAPI publishes the complete contract at
`/openapi.json`, allowing any system or channel gateway to integrate without Watcher shipping a
provider-specific connector.

FastAPI is the maintained Python implementation layer over this HTTP contract. Pydantic models
remain the schema source of truth; FastAPI projects them into OpenAPI 3, serves interactive Swagger
UI at `/docs` and ReDoc at `/redoc`, and advertises the `PropertySystemApiKey` security scheme.
Stable explicit operation IDs allow integration platforms to generate clients without binding
Watcher to their SDKs.

## Errors and safety

* Unknown API keys return `401` without calling the port.
* Missing properties/reservations return `404`.
* Provider timeouts/unavailability return `503`; callers must hand off and must not guess.
* Reservation reads without verified identity return `403` before calling the port. A caller
  cannot assert verification with a request header; verification is part of the server-resolved
  API principal.
* Tenant IDs are passed to every port operation and never accepted from public payloads.

## Fixtures

Tests use a recording fake with two tenants. They assert tenant isolation, OpenAPI schema exposure,
live availability calls (including repeated calls), identity gating, not-found behavior, and safe
provider-failure behavior.
