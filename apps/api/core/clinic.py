"""The clinic domain: branches, services, availability and bookings (demo steps 3–6).

**Scope.** This is the demo vertical's transactional core, and it is deliberately narrow. It holds
the value objects and the one policy constant the schema, the importer and (later) the booking
tools all have to agree on. It holds no persistence and no I/O, the same split
``core/property.py`` makes against ``db/property_repo.py``.

**Natural keys, not database ids.** Every dataclass here is identified by the key the clinic's own
records use — a branch by its ``external_id``, a service by its catalogue ``code`` (DT001…), a slot
by its ``external_id``, a booking by its ``reference`` — and none of them carries a row id. That is
not an oversight: these objects exist on both sides of persistence. The importer builds them from a
workbook that has never seen a UUID, and ``db/clinic_repo.py`` upserts *on those keys*, so a second
import of the same file updates the rows it already wrote instead of duplicating them. A row id
would be null on one side and meaningful on the other, which is the kind of field that gets read
without checking.

**The 15-minute buffer, and where it does and does not apply.** The client settled this
(decision 3, and deviation 2 of the source-data review): the workbook is authoritative for what is
already in the diary, including the 62 adjacent pairs with no gap between them — every 60-minute
service in an hourly grid. So :data:`BOOKING_BUFFER_MINUTES` is *not* an import rule. Nothing in
the importer may reject or adjust a slot for violating it; the importer only counts the pairs so a
human can see the number has not moved. The buffer constrains *new* bookings, which is step 6's
job and is not built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Self

from apps.api.core.emergency import normalise

#: Minutes a *new* booking must leave clear of an existing one. See the module docstring for why
#: the importer counts violations rather than rejecting them.
BOOKING_BUFFER_MINUTES = 15

#: What a slot can be. ``held`` is written only by step 6's ``hold_slot`` and never by an import.
SlotStatus = Literal["open", "held", "booked"]

#: Where a booking came from. ``workbook`` rows are the 265 already in the client's diary;
#: ``bot`` is a booking this system made; ``staff`` is one a person made in the clinic.
BookingSource = Literal["workbook", "bot", "staff"]

#: Punctuation and separators inside a service name that carry no meaning for matching:
#: "Primelase 6-Sessions" and "Primelase 6 Sessions" are the same service written twice.
_SEPARATORS = re.compile(r"[-_/\\,.()\[\]{}:;+&|]+")


def normalise_service_name(name: str) -> str:
    """Fold a service name to the form both sides of a lookup are written in.

    Built on ``core/emergency.normalise`` rather than beside it — the Arabic mark-stripping and
    letter-folding tables exist once, and a name written with a hamza variant must match the same
    way a symptom report does. The one addition is separators: emergency matching reads punctuation
    as a word boundary and needs it kept, a catalogue lookup does not.
    """
    return normalise(_SEPARATORS.sub(" ", name)).strip()


class BookingReferenceError(ValueError):
    """A booking reference that is not of the form the clinic issues."""


@dataclass(frozen=True, slots=True, order=True)
class BookingReference:
    """A human-quotable booking reference, ``DC-0042`` shaped: a tenant prefix and a serial.

    The prefix is a *parameter*, never a constant in this file. It is the clinic's own initials and
    belongs with the tenant's configuration, the same way the conversation copy does; a default
    here would put one client's identifier in shared code and would silently be quoted at the next.

    Only the demo's own references are structured. An imported reference from the workbook is
    stored as the free text the clinic wrote (``Booking.reference``), because it is the clinic's
    identifier and reformatting it would break the number a patient already holds. This type is
    what step 6 will *issue* with, and what the importer uses to read the highest serial already
    taken so a newly issued reference cannot collide with one in the diary.
    """

    prefix: str
    serial: int

    #: ``DC-0042``: two to six letters, a hyphen, and at least four digits.
    PATTERN = re.compile(r"^(?P<prefix>[A-Za-z]{2,6})-(?P<serial>\d{4,})$")

    def __post_init__(self) -> None:
        if not self.prefix.isalpha() or not 2 <= len(self.prefix) <= 6:
            raise BookingReferenceError(f"prefix must be 2–6 letters, got {self.prefix!r}")
        if self.serial < 1:
            raise BookingReferenceError(f"serial must be positive, got {self.serial}")

    def __str__(self) -> str:
        return f"{self.prefix.upper()}-{self.serial:04d}"

    @classmethod
    def parse(cls, text: str) -> Self:
        """The reference ``text`` denotes, or raise. See :meth:`match` for the tolerant form."""
        found = cls.PATTERN.match(text.strip())
        if found is None:
            raise BookingReferenceError(f"not a booking reference: {text!r}")
        return cls(prefix=found["prefix"].upper(), serial=int(found["serial"]))

    @classmethod
    def match(cls, text: str) -> Self | None:
        """The reference ``text`` denotes, or ``None`` — for reading a column that holds both
        structured references and whatever else a clinic has historically typed into it."""
        try:
            return cls.parse(text)
        except BookingReferenceError:
            return None

    def next(self) -> Self:
        """The reference after this one. Step 6 allocates from the highest serial in the diary."""
        return type(self)(prefix=self.prefix, serial=self.serial + 1)


@dataclass(frozen=True, slots=True)
class Branch:
    """One clinic location (``db/models.py``'s ``ClinicBranch``).

    ``placeholder`` marks the five of the client's fourteen branches that are stand-ins for
    locations whose real details have not been supplied (decision 5: all fourteen are in the demo).
    It is a flag on the data, not a filter: the importer sets it, and nothing in this step hides a
    placeholder branch. What reads it is the client pack and whatever step 6 decides a placeholder
    may be booked into.
    """

    external_id: str
    name: str
    area: str | None = None
    address: str | None = None
    phone: str | None = None
    timezone: str | None = None
    placeholder: bool = False
    active: bool = True


@dataclass(frozen=True, slots=True)
class Service:
    """One treatment or treatment package in the catalogue (``ClinicService``).

    ``price_minor`` is in minor units — piastres for EGP — because a price that is quoted to a
    patient must not have been through a float. ``session_count`` is the package quantity, which
    the vocabulary requires every quote to state: 3,100 for one Primelase session and 15,000 for
    six are the same modality at different quantities, and a quote that omits which one it is is
    the failure the ``quoting`` block in the clinic vocabulary is written to prevent.

    ``aliases`` are the other names the same service is called by, normalised at match time. The
    source workbook needs them: "Basic Facial" and "Facial" are one 750/45min service under two
    names, and without a canonical id behind both, a patient saying either loops the assistant
    against its 2-turn clarification limit.
    """

    code: str
    name: str
    price_minor: int
    duration_minutes: int
    currency: str = "EGP"
    session_count: int = 1
    category: str | None = None
    aliases: tuple[str, ...] = ()
    active: bool = True

    @property
    def names(self) -> tuple[str, ...]:
        """Every name this service answers to, canonical first."""
        return (self.name, *self.aliases)


@dataclass(frozen=True, slots=True)
class AvailabilitySlot:
    """One bookable interval in one branch for one service (``ClinicAvailabilitySlot``).

    Times are timezone-aware. The workbook holds a date and a wall-clock time and says nothing
    about which zone it means, so the zone is supplied to the importer explicitly and is never
    defaulted — ``TENANT_TIMEZONE`` still ships defaulting to ``Asia/Dubai``, an hour off Cairo,
    and a silent default here is how 672 slots land an hour out.
    """

    external_id: str
    branch_external_id: str
    service_code: str
    starts_at: datetime
    ends_at: datetime
    status: SlotStatus = "open"

    @property
    def duration_minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    def overlaps(self, other: AvailabilitySlot) -> bool:
        """Whether two slots in the same branch cover any of the same minute.

        Touching is not overlapping: a slot ending at 12:00 and one starting at 12:00 are the
        back-to-back pair decision 3 accepted, not a double-booked room.
        """
        if self.branch_external_id != other.branch_external_id:
            return False
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at

    def gap_minutes_before(self, later: AvailabilitySlot) -> int:
        """Minutes between this slot ending and ``later`` starting. Negative if they overlap."""
        return int((later.starts_at - self.ends_at).total_seconds() // 60)


@dataclass(frozen=True, slots=True)
class Booking:
    """An appointment against one slot (``ClinicBooking``).

    ``idempotency_key`` is what makes step 6's confirm safe to retry: the same conversation
    confirming the same slot twice — a duplicate webhook, a patient tapping send twice — must
    produce one appointment. Built by :func:`booking_idempotency_key`, unique per tenant in the
    database. Imported bookings have neither a conversation nor a key; the unique
    ``(tenant, slot)`` constraint is what stops those being doubled.
    """

    reference: str
    slot_external_id: str
    source: BookingSource
    patient_name: str | None = None
    patient_phone: str | None = None
    status: Literal["confirmed", "cancelled"] = "confirmed"
    conversation_id: str | None = None
    idempotency_key: str | None = None
    #: Kept out of equality: two bookings of the same slot by the same conversation are the same
    #: booking whatever the notes say.
    notes: str | None = field(default=None, compare=False)


def booking_idempotency_key(tenant_id: str, conversation_id: str, slot_external_id: str) -> str:
    """The key one conversation booking one slot is idempotent on (handoff §7.2).

    Tenant included even though the uniqueness constraint is already per tenant: the key is written
    into a column, read in logs, and compared by people, and one that is unique only in context is
    the kind that gets compared out of it.
    """
    return f"{tenant_id}:{conversation_id}:{slot_external_id}"
