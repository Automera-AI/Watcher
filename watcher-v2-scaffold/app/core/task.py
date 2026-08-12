"""The job the receptionist is trying to finish, and the blanks it still needs filled.

A receptionist is not a question answering machine. It is holding a goal across several
messages: get me a name, a date, a unit, then book it. This is that, as a small state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.understanding import PropertyIntent

#: What each job needs before it can be carried out.
REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    PropertyIntent.BOOKING_ENQUIRY: ("check_in", "check_out", "guests", "unit_type"),
    PropertyIntent.AVAILABILITY_CHECK: ("check_in", "check_out"),
    PropertyIntent.MODIFY_RESERVATION: ("reservation_ref", "change_requested"),
    PropertyIntent.CANCEL_RESERVATION: ("reservation_ref",),
    PropertyIntent.MAINTENANCE_ISSUE: ("unit", "issue_description"),
    PropertyIntent.VIEWING_REQUEST: ("unit", "preferred_time", "contact_name"),
    PropertyIntent.ACCESS_CODE_REQUEST: ("reservation_ref",),
}

#: Details we always read back to the customer before acting, because getting them wrong is
#: expensive and speech recognition is not perfect. See the spec, section on spoken Arabic.
ALWAYS_CONFIRM: frozenset[str] = frozenset(
    {"check_in", "check_out", "reservation_ref", "unit", "phone", "guests"}
)


class TaskStatus(StrEnum):
    COLLECTING = "collecting"
    READY = "ready"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"


@dataclass
class Task:
    intent: PropertyIntent
    slots: dict[str, str] = field(default_factory=dict)
    confirmed: set[str] = field(default_factory=set)
    status: TaskStatus = TaskStatus.COLLECTING

    @property
    def required(self) -> tuple[str, ...]:
        return REQUIRED_SLOTS.get(self.intent, ())

    @property
    def missing(self) -> list[str]:
        return [s for s in self.required if not self.slots.get(s)]

    @property
    def unconfirmed(self) -> list[str]:
        return [
            s for s in self.required
            if s in ALWAYS_CONFIRM and self.slots.get(s) and s not in self.confirmed
        ]

    def absorb(self, new_slots: dict[str, str]) -> None:
        """Take in details from the latest message.

        A detail that changes value loses its confirmation. If the guest said the 4th and now
        says the 5th, we ask again rather than quietly booking the 5th.
        """
        for key, value in new_slots.items():
            if not value:
                continue
            if self.slots.get(key) not in (None, value):
                self.confirmed.discard(key)
            self.slots[key] = value

    def next_step(self) -> tuple[str, str | None]:
        """What to do next: ask for a detail, read one back, or go ahead."""
        if missing := self.missing:
            return "ask", missing[0]
        if unconfirmed := self.unconfirmed:
            return "confirm", unconfirmed[0]
        return "execute", None
