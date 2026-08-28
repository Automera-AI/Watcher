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
    """Unified intent taxonomy — the union of every vertical's vocabulary.

    The classifier and the receptionist share one taxonomy so decide_autonomy() recognises
    classified intents and the receptionist fires on real messages.

    **Why this is a union rather than one vertical's list.** This enum types the classifier's
    structured output, so a model physically cannot return an intent that is not a member. The
    vocabularies are data and a tenant picks one; this is code and ships once. Keeping the union
    here means adding a vertical needs no change to the classifier contract, at the cost of the
    enum naming intents a given tenant will never see. The vocabulary is what decides which are
    *live* for a tenant — an intent it does not declare is unknown to ``decide_autonomy``, which
    hands off rather than improvising, so a cross-vertical leak fails safe.

    Grouped by where they came from. Several are shared: a greeting is a greeting in any vertical.
    """

    # ── Shared across verticals ──────────────────────────────────────────────
    GREETING = "greeting"
    THANKS_CLOSING = "thanks_closing"
    GENERAL_INFO = "general_info"
    DIRECTIONS = "directions"
    BILLING_QUESTION = "billing_question"
    COMPLAINT = "complaint"
    SPAM = "spam"
    UNCLEAR = "unclear"
    AVAILABILITY_CHECK = "availability_check"
    PRICE_ENQUIRY = "price_enquiry"
    BOOKING_ENQUIRY = "booking_enquiry"
    #: An explicit ask for a person. Distinct from every other hand-off: the customer requested
    #: it rather than the receptionist running out of road, and it needs no justification.
    HUMAN_REQUEST = "human_request"
    PROMOTION_ENQUIRY = "promotion_enquiry"
    PAYMENT_OPTIONS = "payment_options"
    PRIVACY_DATA_REQUEST = "privacy_data_request"
    CAREERS_BUSINESS_ENQUIRY = "careers_business_enquiry"
    POSITIVE_FEEDBACK = "positive_feedback"
    LOST_PROPERTY = "lost_property"
    DIGITAL_SUPPORT_ISSUE = "digital_support_issue"

    # ── Clinics ──────────────────────────────────────────────────────────────
    SERVICE_QUESTION = "service_question"
    PACKAGE_TERMS_QUESTION = "package_terms_question"
    PREPARATION_AFTERCARE_INFO = "preparation_aftercare_info"
    PRACTITIONER_AVAILABILITY = "practitioner_availability"
    NAMED_PRACTITIONER_REQUEST = "named_practitioner_request"
    APPOINTMENT_LOOKUP_STATUS = "appointment_lookup_status"
    MODIFY_APPOINTMENT = "modify_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    ARRIVAL_LATE_NO_SHOW = "arrival_late_no_show"
    PACKAGE_ACCOUNT_STATUS = "package_account_status"
    PRODUCT_VOUCHER_ENQUIRY = "product_voucher_enquiry"
    STOCK_ENQUIRY = "stock_enquiry"
    ONLINE_ORDER_SUPPORT = "online_order_support"
    MEDICAL_DOCUMENTS_RESULTS = "medical_documents_results"
    FOLLOWUP_TOUCHUP_REVIEW = "followup_touchup_review"
    #: Suitability, medical history, "is this safe for me". Never answered by the receptionist.
    CLINICAL_QUESTION = "clinical_question"
    #: A reaction after treatment that needs a clinician soon. Never assessed, always escalated.
    CLINICAL_URGENT = "clinical_urgent"

    # ── Holiday homes ────────────────────────────────────────────────────────
    PROPERTY_QUESTION = "property_question"
    MODIFY_RESERVATION = "modify_reservation"
    CANCEL_RESERVATION = "cancel_reservation"
    CHECK_IN_SUPPORT = "check_in_support"
    ACCESS_CODE_REQUEST = "access_code_request"
    MAINTENANCE_ISSUE = "maintenance_issue"
    EXTEND_STAY = "extend_stay"
    CHECKOUT_QUESTION = "checkout_question"
    PAYMENT_QUESTION = "payment_question"
    OWNER_ENQUIRY = "owner_enquiry"


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
