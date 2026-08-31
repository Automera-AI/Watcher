"""The job the receptionist is trying to finish, and the blanks it still needs filled.

A receptionist is not a question-answering machine. It is holding a goal across several
messages: get me a date, a unit, a headcount, then hold it. This is that, as a small state
machine. Ported from the v2 scaffold in roadmap 1.2.

**One change from the scaffold, and it is the reason 1.2 waited on 0.3.** The scaffold carried
its own ``REQUIRED_SLOTS`` dict and its own ``ALWAYS_CONFIRM`` set, hardcoded next to a second
copy of the intent list. Both now come from the vocabulary, so there is one place a slot is
declared and no way for the two to drift. The values happen to agree today — that is precisely
the kind of agreement that stops being true six months in.

Intents are plain strings validated against the vocabulary rather than a second enum, for the
same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from packages.intents.schema import Vocabulary, default_vocabulary

#: Reserved task-slot namespace for state-machine metadata persisted without a schema migration.
#: Classifier output may never write these keys; patient/business surfaces consume only vocabulary
#: slots or explicitly named facts, while these values remain in ``Task.slots`` across rehydration.
INTERNAL_SLOT_PREFIX = "__watcher_internal__"
AWAITING_ANOTHER_DATE_SLOT = f"{INTERNAL_SLOT_PREFIX}awaiting_another_date"
NON_PROGRESS_TURNS_SLOT = f"{INTERNAL_SLOT_PREFIX}non_progress_turns"


def is_internal_slot(name: str) -> bool:
    """Whether ``name`` is reserved persisted state rather than a patient/business fact."""
    return name.startswith(INTERNAL_SLOT_PREFIX)


class TaskStatus(StrEnum):
    COLLECTING = "collecting"
    READY = "ready"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"
    #: The guest moved on to something else before this job finished. Not a failure — nothing
    #: went wrong — but it is over, and it must leave the active set or the next message keeps
    #: resuming a job nobody is asking about (roadmap A5).
    ABANDONED = "abandoned"


class UnknownIntent(ValueError):
    """The task was opened for something the vocabulary does not define."""


@dataclass
class Task:
    """One in-flight job. ``slots`` is what we have; ``confirmed`` is what was agreed."""

    intent: str
    slots: dict[str, str] = field(default_factory=dict)
    confirmed: set[str] = field(default_factory=set)
    status: TaskStatus = TaskStatus.COLLECTING
    vocabulary: Vocabulary = field(default_factory=default_vocabulary, repr=False)

    def __post_init__(self) -> None:
        if self.intent not in {i.name for i in self.vocabulary.intents}:
            raise UnknownIntent(f"{self.intent!r} is not in the vocabulary")

    @property
    def required(self) -> tuple[str, ...]:
        """The details this intent cannot proceed without, in the order they are asked for."""
        intent = next(i for i in self.vocabulary.intents if i.name == self.intent)
        return tuple(intent.required_slots)

    @property
    def _confirmable(self) -> frozenset[str]:
        """Details expensive enough to get wrong that they are read back first.

        The intent may override the file-level default — ``modify_reservation`` reads back the
        change itself, which no other intent collects.
        """
        intent = next(i for i in self.vocabulary.intents if i.name == self.intent)
        if (override := intent.confirm_before_acting) is not None:
            return frozenset(override)
        return frozenset(self.vocabulary.defaults.confirm_before_acting)

    @property
    def missing(self) -> list[str]:
        return [s for s in self.required if not self.slots.get(s)]

    @property
    def unconfirmed(self) -> list[str]:
        return [
            s
            for s in self.required
            if s in self._confirmable and self.slots.get(s) and s not in self.confirmed
        ]

    def absorb(self, new_slots: dict[str, str]) -> None:
        """Take in details from the latest message.

        **A detail that changes value loses its confirmation.** If the guest said the 4th and
        now says the 5th, we ask again rather than quietly booking the 5th. Agreement attaches
        to a value, not to a slot — without this a task accumulates consent it never got.

        Re-stating the same value keeps the confirmation, so a guest repeating themselves does
        not reset the conversation. Blank values are ignored: a model that failed to extract a
        date must not erase the one we already have.
        """
        for key, value in new_slots.items():
            if not value or is_internal_slot(key):
                continue
            if self.slots.get(key) not in (None, value):
                self.confirmed.discard(key)
            self.slots[key] = value

    @property
    def awaiting_agreement(self) -> bool:
        """Whether a read-back is outstanding: everything collected, nothing agreed to yet.

        This *is* the pending-confirmation state the clinic vocabulary's dialogue-state rule talks
        about, derived rather than stored. There is no third thing to remember — a task with
        nothing missing and something unconfirmed has just read those details back and is waiting
        for an answer, which is why no column and no migration are needed to know it.
        """
        return not self.missing and bool(self.unconfirmed)

    def agree(self) -> None:
        """The customer said yes to what was read back to them.

        Everything outstanding at once, because they were read back at once. Before this existed
        ``confirmed`` was a set that was only ever *emptied* — ``absorb`` discards from it and
        nothing added — so an intent declaring ``confirm_before_acting`` read a detail back, was
        told "أيوه", and read it back again until the clarifying-turn limit fetched a person. No
        task with a confirmable slot could reach ``execute``, which is why ``confirm_booking``
        was unreachable even once it was built.
        """
        self.confirmed.update(self.unconfirmed)

    def next_step(self) -> tuple[str, str | None]:
        """What to do next: ask for a detail, read one back, or go ahead."""
        if missing := self.missing:
            return "ask", missing[0]
        if unconfirmed := self.unconfirmed:
            return "confirm", unconfirmed[0]
        return "execute", None
