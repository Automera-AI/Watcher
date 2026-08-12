"""Tasks and slot filling: what the receptionist is part-way through doing (1.2).

**What trap #4 was about.** The scaffold used ``booking_enquiry`` and ``availability_check``,
neither of which existed in the old six-intent lead-capture taxonomy — which is why 1.2 was
blocked on 0.3. Both are now real intents in `packages/intents/intents.yaml`, so this file reads
its expectations out of the vocabulary rather than restating them and drifting.

**The rule that had to survive porting: changing a date cancels its confirmation.** A guest who
says "the 4th to the 9th", is read back "the 4th to the 9th", agrees, and then says "actually the
6th" has *not* agreed to the 6th. Anything already confirmed that depended on the old value goes
back to unconfirmed. Without it a task accumulates agreement it never got, and the guest is held
to dates they corrected.

The vocabulary half is live. Tasks, slots and conversations are item 2.1, so those are
`xfail(strict=True)`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from packages.intents import schema

VOCAB = schema.load(Path(__file__).resolve().parents[3] / "packages/intents/intents.yaml")
BY_NAME = {i.name: i for i in VOCAB.intents}


# ── live today: the intents this file is built on now exist (0.3) ─────────────


def test_the_intents_the_scaffold_assumed_are_real_now() -> None:
    """Trap #4, closed. These two were the reason 1.2 waited on 0.3."""
    assert "booking_enquiry" in BY_NAME
    assert "availability_check" in BY_NAME


def test_a_booking_enquiry_knows_which_details_it_must_collect() -> None:
    """Slot filling needs somewhere to read the required set from, and this is it."""
    booking = BY_NAME["booking_enquiry"]
    assert set(booking.required_slots) == {"check_in", "check_out", "guests", "unit_type"}
    assert booking.terminal_tool == "hold_slot"


def test_a_booking_enquiry_holds_a_unit_and_never_confirms_one() -> None:
    """The worst case is a unit briefly off sale, not a booking nobody agreed to."""
    booking = BY_NAME["booking_enquiry"]
    assert booking.max_autonomy == "act_and_notify"
    joined = " ".join(booking.never).lower()
    assert "confirm a booking" in joined
    assert "payment" in joined or "card details" in joined


def test_the_dates_are_read_back_before_anything_acts_on_them() -> None:
    """``confirm_before_acting`` is what the cancel-on-change rule below operates on."""
    assert {"check_in", "check_out"} <= set(VOCAB.defaults.confirm_before_acting)


def test_clarifying_questions_are_bounded_and_end_at_a_person() -> None:
    """A task that cannot fill its slots must terminate, not interrogate."""
    assert 1 <= VOCAB.defaults.max_clarifying_turns <= 5
    assert VOCAB.defaults.on_max_turns == "handoff_to_human"


# ── specification for item 2.1 ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="tasks and slot filling are roadmap item 2.1")
def test_a_task_is_not_ready_until_its_required_slots_are_filled() -> None:
    from apps.api.conversations.task import Task

    task = Task.for_intent("booking_enquiry")
    assert not task.is_ready
    task.fill(check_in="2026-09-04", check_out="2026-09-09", guests=4)
    assert not task.is_ready, "unit_type is still missing"
    task.fill(unit_type="2 bed")
    assert task.is_ready


@pytest.mark.xfail(strict=True, reason="tasks and slot filling are roadmap item 2.1")
def test_changing_a_date_cancels_its_confirmation() -> None:
    """The rule worth porting. Agreement is to a value, not to a slot."""
    from apps.api.conversations.task import Task

    task = Task.for_intent("booking_enquiry")
    task.fill(check_in="2026-09-04", check_out="2026-09-09", guests=4, unit_type="2 bed")
    task.confirm("check_in", "check_out")
    assert task.is_confirmed("check_in")

    task.fill(check_in="2026-09-06")
    assert not task.is_confirmed("check_in"), "the guest agreed to the 4th, not the 6th"
    assert task.is_confirmed("check_out"), "an unrelated slot should keep its agreement"


@pytest.mark.xfail(strict=True, reason="tasks and slot filling are roadmap item 2.1")
def test_refilling_a_slot_with_the_same_value_keeps_its_confirmation() -> None:
    """Otherwise a guest repeating themselves resets the conversation."""
    from apps.api.conversations.task import Task

    task = Task.for_intent("booking_enquiry")
    task.fill(check_in="2026-09-04")
    task.confirm("check_in")
    task.fill(check_in="2026-09-04")
    assert task.is_confirmed("check_in")


@pytest.mark.xfail(strict=True, reason="tasks and slot filling are roadmap item 2.1")
def test_a_task_cannot_run_its_terminal_tool_unconfirmed() -> None:
    from apps.api.conversations.task import Task, TaskNotConfirmed

    task = Task.for_intent("booking_enquiry")
    task.fill(check_in="2026-09-04", check_out="2026-09-09", guests=4, unit_type="2 bed")
    with pytest.raises(TaskNotConfirmed):
        task.run()


@pytest.mark.xfail(strict=True, reason="tasks and slot filling are roadmap item 2.1")
def test_a_task_gives_up_after_the_configured_number_of_questions() -> None:
    from apps.api.conversations.task import Task

    task = Task.for_intent("booking_enquiry")
    for _ in range(VOCAB.defaults.max_clarifying_turns):
        task.ask()
    assert task.next_action == "handoff_to_human"
