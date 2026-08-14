"""Controlled vocabularies for the Watcher data model.

Only values that the build-spec addendum §4 (or v1.2 §3) explicitly enumerates are hard enums here.
Open-vocabulary fields (``intent``, ``suggested_record_type``) are typed as ``str`` in the models
with a docstring pointer to v1.2 §3 — we do not invent a taxonomy the product spec hasn't pinned.
"""

from __future__ import annotations

from enum import StrEnum


class TenantTier(StrEnum):
    """Deployment tier of a tenant (addendum §3)."""

    SAAS = "saas"
    SELF_HOSTED = "self_hosted"


class SourceKind(StrEnum):
    """Whether a watched conversation is 1:1 or a group (addendum §4 ``sources``)."""

    DIRECT = "direct"
    GROUP = "group"


class MessageDirection(StrEnum):
    """Inbound (from a contact) vs outbound (sent by the business)."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class IntentType(StrEnum):
    """Unified intent taxonomy — aligned with the receptionist vocabulary (intents.yaml).

    The classifier and the receptionist now share one taxonomy so decide_autonomy()
    recognises classified intents and the receptionist fires on real messages.
    """

    AVAILABILITY_CHECK = "availability_check"
    PRICE_ENQUIRY = "price_enquiry"
    BOOKING_ENQUIRY = "booking_enquiry"
    PROPERTY_QUESTION = "property_question"
    MODIFY_RESERVATION = "modify_reservation"
    CANCEL_RESERVATION = "cancel_reservation"
    CHECK_IN_SUPPORT = "check_in_support"
    ACCESS_CODE_REQUEST = "access_code_request"
    DIRECTIONS = "directions"
    MAINTENANCE_ISSUE = "maintenance_issue"
    EXTEND_STAY = "extend_stay"
    CHECKOUT_QUESTION = "checkout_question"
    BILLING_QUESTION = "billing_question"
    PAYMENT_QUESTION = "payment_question"
    OWNER_ENQUIRY = "owner_enquiry"
    COMPLAINT = "complaint"
    GENERAL_INFO = "general_info"
    SPAM = "spam"
    UNCLEAR = "unclear"


class RecordType(StrEnum):
    """Shape of the destination record to create — locked taxonomy (DECISIONS.md)."""

    INDIVIDUAL_ONLY = "individual_only"
    CONTACT_UNDER_COMPANY = "contact_under_company"
    COMPANY_ONLY = "company_only"


class MessageType(StrEnum):
    """Message modality (addendum §4 ``messages.type``)."""

    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    OTHER = "other"


class Language(StrEnum):
    """Detected content language. Arabic from day one, mixed runs expected (addendum §9, §15)."""

    AR = "ar"
    EN = "en"
    MIXED = "mixed"
    OTHER = "other"


class ConfidenceBand(StrEnum):
    """Routing band derived from the overall confidence (v1.2 §3 rubric; DESIGN-SPEC §7).

    ``high`` auto-routes, ``medium`` pings the control chat, ``low`` drops straight to the inbox.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InboxStatus(StrEnum):
    """Lifecycle of an inbox item (addendum §4 ``inbox_items.status``)."""

    PENDING = "pending"
    AUTO_ROUTED = "auto_routed"
    CONFIRMED = "confirmed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


class IdentityDecision(StrEnum):
    """Outcome of identity resolution for a message (addendum §4, §9)."""

    MERGE = "merge"
    LINK_RELATED = "link_related"
    NEW = "new"


class DestinationKind(StrEnum):
    """Where structured records are routed (addendum §4 ``destinations.kind``, §11)."""

    GOOGLE_SHEETS = "google_sheets"
    WEBHOOK = "webhook"
