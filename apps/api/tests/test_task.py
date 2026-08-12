"""The task state machine (ported from the v2 scaffold, roadmap 1.2).

All four scaffold tests are here, behaviour unchanged. Two things differ in how they are
written, both because of 0.3:

* intents are vocabulary names rather than a second ``PropertyIntent`` enum;
* the required slots and the read-back set come from ``intents.yaml``, so this file no longer
  restates them. **This is why 1.2 was blocked on 0.3** — trap #4 was that the scaffold used
  ``booking_enquiry`` and ``availability_check``, which the old six-intent taxonomy did not have.

The rule worth the whole file: **changing a date cancels its confirmation.**
"""

from __future__ import annotations

import pytest

from apps.api.conversations.task import Task, TaskStatus, UnknownIntent

# ── ported from the scaffold ──────────────────────────────────────────────────


def test_asks_for_the_first_missing_detail() -> None:
    t = Task(intent="booking_enquiry")
    step, slot = t.next_step()
    assert step == "ask"
    assert slot == "check_in"


def test_reads_back_details_before_acting() -> None:
    t = Task(intent="availability_check")
    t.absorb({"check_in": "2026-09-04", "check_out": "2026-09-09"})
    step, _slot = t.next_step()
    assert step == "confirm"


def test_changing_a_date_cancels_its_confirmation() -> None:
    """The guest said the 4th, we confirmed it, then they said the 5th. Ask again.

    Agreement attaches to a value, not to a slot. Without this a task quietly accumulates
    consent it never got, and the guest is held to dates they corrected.
    """
    t = Task(intent="availability_check")
    t.absorb({"check_in": "2026-09-04", "check_out": "2026-09-09"})
    t.confirmed.update({"check_in", "check_out"})
    assert t.next_step()[0] == "execute"

    t.absorb({"check_in": "2026-09-05"})
    assert "check_in" not in t.confirmed
    assert t.next_step() == ("confirm", "check_in")


def test_blank_values_are_ignored() -> None:
    """A model that failed to extract a date must not erase the one we already have."""
    t = Task(intent="availability_check")
    t.absorb({"check_in": "2026-09-04"})
    t.absorb({"check_in": ""})
    assert t.slots["check_in"] == "2026-09-04"


# ── added: the edges the scaffold left open ───────────────────────────────────


def test_restating_the_same_value_keeps_its_confirmation() -> None:
    """Otherwise a guest repeating themselves resets the conversation."""
    t = Task(intent="availability_check")
    t.absorb({"check_in": "2026-09-04"})
    t.confirmed.add("check_in")
    t.absorb({"check_in": "2026-09-04"})
    assert "check_in" in t.confirmed


def test_changing_one_detail_leaves_the_others_agreed() -> None:
    t = Task(intent="availability_check")
    t.absorb({"check_in": "2026-09-04", "check_out": "2026-09-09"})
    t.confirmed.update({"check_in", "check_out"})
    t.absorb({"check_in": "2026-09-05"})
    assert "check_out" in t.confirmed


def test_the_required_slots_come_from_the_vocabulary() -> None:
    """Trap #4, closed: these intents are real now, and declared in one place only."""
    assert Task(intent="booking_enquiry").required == (
        "check_in",
        "check_out",
        "guests",
        "unit_type",
    )
    assert Task(intent="availability_check").required == ("check_in", "check_out")


def test_an_intent_override_beats_the_file_default() -> None:
    """``modify_reservation`` reads back the change itself, which nothing else collects."""
    t = Task(intent="modify_reservation")
    t.absorb({"reservation_ref": "ABC123", "change_requested": "move to the 6th"})
    assert t.next_step() == ("confirm", "reservation_ref")
    t.confirmed.add("reservation_ref")
    assert t.next_step() == ("confirm", "change_requested")


def test_a_task_cannot_be_opened_for_an_intent_nobody_declared() -> None:
    with pytest.raises(UnknownIntent):
        Task(intent="arrange_airport_pickup")


def test_a_new_task_starts_collecting() -> None:
    assert Task(intent="booking_enquiry").status is TaskStatus.COLLECTING
