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
    """Whether a watched WhatsApp conversation is 1:1 or a group (addendum §4 ``sources``)."""

    DIRECT = "direct"
    GROUP = "group"


class MessageDirection(StrEnum):
    """Inbound (from a contact) vs outbound (sent by the business)."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class IntentType(StrEnum):
    """Why an inbound message exists — locked taxonomy (DECISIONS.md / role guide §1.1)."""

    NEW_LEAD = "new_lead"
    EXISTING_CONTACT_REPLY = "existing_contact_reply"
    SUPPORT_ISSUE = "support_issue"
    INTERNAL_TEAM = "internal_team"
    SPAM_OR_NOISE = "spam_or_noise"
    UNCLEAR = "unclear"


class RecordType(StrEnum):
    """Shape of the destination record to create — locked taxonomy (DECISIONS.md)."""

    INDIVIDUAL_ONLY = "individual_only"
    CONTACT_UNDER_COMPANY = "contact_under_company"
    COMPANY_ONLY = "company_only"


class MessageType(StrEnum):
    """WhatsApp message modality (addendum §4 ``messages.type``)."""

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
