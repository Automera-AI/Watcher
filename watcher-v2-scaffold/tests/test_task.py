from app.core.task import Task, TaskStatus
from app.core.understanding import PropertyIntent


def test_asks_for_the_first_missing_detail():
    t = Task(intent=PropertyIntent.BOOKING_ENQUIRY)
    step, slot = t.next_step()
    assert step == "ask"
    assert slot == "check_in"


def test_reads_back_details_before_acting():
    t = Task(intent=PropertyIntent.AVAILABILITY_CHECK)
    t.absorb({"check_in": "2026-09-04", "check_out": "2026-09-09"})
    step, slot = t.next_step()
    assert step == "confirm"


def test_changing_a_date_cancels_its_confirmation():
    """The guest said the 4th, we confirmed it, then they said the 5th. Ask again."""
    t = Task(intent=PropertyIntent.AVAILABILITY_CHECK)
    t.absorb({"check_in": "2026-09-04", "check_out": "2026-09-09"})
    t.confirmed.update({"check_in", "check_out"})
    assert t.next_step()[0] == "execute"

    t.absorb({"check_in": "2026-09-05"})
    assert "check_in" not in t.confirmed
    assert t.next_step() == ("confirm", "check_in")


def test_blank_values_are_ignored():
    t = Task(intent=PropertyIntent.AVAILABILITY_CHECK)
    t.absorb({"check_in": "2026-09-04"})
    t.absorb({"check_in": ""})
    assert t.slots["check_in"] == "2026-09-04"
