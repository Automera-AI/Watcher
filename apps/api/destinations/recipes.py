"""Webhook recipes and payload mapping (addendum §11).

A recipe is a versioned, named mapping from internal classification fields to a destination's
payload keys. ``field_mapping`` (stored per destination) maps ``output_key -> internal_field``; the
payload is built by reading those fields off the classification. Ship the three Phase-2 recipes
(HubSpot, Pipedrive, Notion); a Custom destination supplies its own mapping.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.classification import Classification

# Internal classification fields a mapping is allowed to reference.
MAPPABLE_FIELDS = frozenset(
    {
        "intent",
        "summary_one_line",
        "language",
        "person_name",
        "person_appears_to_be",
        "company_name",
        "company_domain_hint",
        "phone_e164",
        "suggested_record_type",
        "confidence_overall",
    }
)


class Recipe(BaseModel):
    """A versioned destination recipe: default ``output_key -> internal_field`` mapping."""

    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    field_mapping: dict[str, str]


# Phase-2 recipes (§11). Mappings are sensible defaults; the mapping UI can override them.
RECIPES: dict[str, Recipe] = {
    "hubspot": Recipe(
        id="hubspot",
        version=1,
        field_mapping={
            "firstname": "person_name",
            "company": "company_name",
            "phone": "phone_e164",
            "lead_note": "summary_one_line",
        },
    ),
    "pipedrive": Recipe(
        id="pipedrive",
        version=1,
        field_mapping={
            "name": "person_name",
            "org_name": "company_name",
            "phone": "phone_e164",
            "note": "summary_one_line",
        },
    ),
    "notion": Recipe(
        id="notion",
        version=1,
        field_mapping={
            "Name": "person_name",
            "Company": "company_name",
            "Phone": "phone_e164",
            "Summary": "summary_one_line",
        },
    ),
}


class UnknownFieldError(ValueError):
    """Raised when a mapping references a field that isn't mappable."""


def build_payload(classification: Classification, field_mapping: dict[str, str]) -> dict[str, Any]:
    """Render a destination payload from a classification via ``output_key -> internal_field``."""
    payload: dict[str, Any] = {}
    for output_key, internal_field in field_mapping.items():
        if internal_field not in MAPPABLE_FIELDS:
            raise UnknownFieldError(internal_field)
        payload[output_key] = getattr(classification, internal_field)
    return payload
