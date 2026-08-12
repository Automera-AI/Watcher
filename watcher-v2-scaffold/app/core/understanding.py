"""Work out what the customer just said.

This is the v1.2 classifier, kept almost whole. The two changes: the list of intents is the
receptionist's list rather than the CRM sync list, and it now also pulls out the details needed
to actually do the job (dates, unit, number of guests).

The confidence bands are the safety system. Read `decide_autonomy` before changing anything here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.settings import settings


class PropertyIntent(StrEnum):
    BOOKING_ENQUIRY = "booking_enquiry"
    AVAILABILITY_CHECK = "availability_check"
    MODIFY_RESERVATION = "modify_reservation"
    CANCEL_RESERVATION = "cancel_reservation"
    CHECK_IN_SUPPORT = "check_in_support"
    ACCESS_CODE_REQUEST = "access_code_request"
    MAINTENANCE_ISSUE = "maintenance_issue"
    BILLING_QUESTION = "billing_question"
    OWNER_ENQUIRY = "owner_enquiry"
    VIEWING_REQUEST = "viewing_request"
    GENERAL_INFO = "general_info"
    SPAM = "spam"
    UNCLEAR = "unclear"


#: Intents we never act on by ourselves, whatever the confidence score says.
ALWAYS_HUMAN: frozenset[str] = frozenset(
    {
        PropertyIntent.BILLING_QUESTION,
        PropertyIntent.OWNER_ENQUIRY,
    }
)

#: Intents that require the caller to prove who they are first.
REQUIRES_VERIFIED_IDENTITY: frozenset[str] = frozenset(
    {
        PropertyIntent.ACCESS_CODE_REQUEST,
        PropertyIntent.MODIFY_RESERVATION,
        PropertyIntent.CANCEL_RESERVATION,
    }
)


class Understanding(BaseModel):
    """What the model returns. Mirrors the `classifications` table one to one."""

    intent: PropertyIntent
    slots: dict[str, str] = Field(default_factory=dict)
    person_name: str | None = None
    language: Literal["en", "ar", "ar-EG", "ar-AE", "mixed"] = "en"
    summary_one_line: str
    reply_draft: str | None = None

    confidence_overall: float = Field(ge=0, le=1)
    confidence_intent: float = Field(ge=0, le=1)
    confidence_slots: float = Field(ge=0, le=1)

    @property
    def band(self) -> Literal["high", "medium", "low"]:
        if self.confidence_overall >= settings.confidence_high:
            return "high"
        if self.confidence_overall >= settings.confidence_low:
            return "medium"
        return "low"


Autonomy = Literal["act", "act_and_notify", "hand_off"]


def decide_autonomy(u: Understanding, *, identity_verified: bool) -> Autonomy:
    """How much the receptionist is allowed to do on its own.

    This is the single most important function in the codebase. Everything the receptionist does
    without a human watching passes through here.
    """
    if u.intent in ALWAYS_HUMAN:
        return "hand_off"
    if u.intent in REQUIRES_VERIFIED_IDENTITY and not identity_verified:
        return "hand_off"
    if u.band == "low":
        return "hand_off"
    if u.band == "medium":
        return "act_and_notify"
    return "act"
