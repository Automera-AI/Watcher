"""Tests for the receptionist function (Item 2.2)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from apps.api.conversations.receptionist import handle
from apps.api.conversations.task import Task, TaskStatus
from apps.api.conversations.tools import REGISTRY, AnswerFromKnowledge
from apps.api.core.knowledge import Fact
from apps.api.schemas.envelope import InboundTurn

TENANT_ID = uuid.uuid4()


def _turn(text: str = "hello") -> InboundTurn:
    return InboundTurn(
        tenant_id=TENANT_ID,
        channel="whatsapp",
        channel_thread_id="thread-1",
        channel_identity="+966500000000",
        modality="text",
        text=text,
        received_at=datetime.now(UTC),
        idempotency_key=f"key-{text}",
    )


def test_hand_off_intent_always_hands_off() -> None:
    action, task = asyncio.run(handle(_turn(), "cancel_reservation", 0.95, {}, None))
    assert action.kind == "handoff"
    assert task.status == TaskStatus.HANDED_OFF


def test_missing_slot_asks() -> None:
    action, task = asyncio.run(
        handle(
            _turn("I want to check availability"),
            "availability_check",
            0.95,
            {},
            None,
        )
    )
    assert action.kind == "ask"
    assert "check in" in action.text.lower()
    assert task.status == TaskStatus.COLLECTING


def test_confirm_flow() -> None:
    task = Task(intent="availability_check", slots={"check_in": "Jan 15", "check_out": "Jan 20"})
    action, task = asyncio.run(handle(_turn(), "availability_check", 0.95, {}, task))
    assert action.kind == "confirm"
    assert task.status == TaskStatus.COLLECTING


def test_slot_change_resets_confirmation() -> None:
    task = Task(
        intent="availability_check",
        slots={"check_in": "Jan 15", "check_out": "Jan 20"},
        confirmed={"check_in"},
    )
    action, task = asyncio.run(
        handle(
            _turn("actually Jan 16"),
            "availability_check",
            0.95,
            {"check_in": "Jan 16"},
            task,
        )
    )
    assert "check_in" not in task.confirmed
    assert task.slots["check_in"] == "Jan 16"
    assert action.kind == "confirm"


def test_all_slots_ready_returns_execute() -> None:
    task = Task(
        intent="availability_check",
        slots={"check_in": "Jan 15", "check_out": "Jan 20"},
        confirmed={"check_in", "check_out"},
    )
    action, task = asyncio.run(handle(_turn(), "availability_check", 0.95, {}, task))
    assert action.kind == "say"
    assert task.status == TaskStatus.COMPLETED


def test_emergency_always_hands_off() -> None:
    action, task = asyncio.run(
        handle(
            _turn("there's a gas leak"),
            "property_question",
            0.99,
            {},
            None,
            emergency=True,
        )
    )
    assert action.kind == "handoff"
    assert task.status == TaskStatus.HANDED_OFF


class _FakeKnowledge:
    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    def search(self, tenant_id: str) -> list[Fact]:
        return self._facts


def test_a_matched_fact_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    """``directions`` names ``answer_from_knowledge`` (roadmap 2.4) — a real match is answered."""
    fact = Fact(
        id="1",
        topic="directions",
        question="how do I get there",
        answer="Take the M1.",
        sensitive=False,
    )
    monkeypatch.setitem(
        REGISTRY, "answer_from_knowledge", AnswerFromKnowledge(_FakeKnowledge([fact]))
    )
    action, task = asyncio.run(handle(_turn("how do I get there"), "directions", 0.95, {}, None))
    assert action.kind == "say"
    assert action.text == "Take the M1."
    assert task.status == TaskStatus.COMPLETED


def test_no_matching_fact_fetches_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real "I don't know" (``on_no_knowledge``) — not a guess, not a false "All set"."""
    monkeypatch.setitem(REGISTRY, "answer_from_knowledge", AnswerFromKnowledge(_FakeKnowledge([])))
    action, task = asyncio.run(handle(_turn("how do I get there"), "directions", 0.95, {}, None))
    assert action.kind == "handoff"
    assert task.status == TaskStatus.HANDED_OFF


#: Deliberately *not* a door code, a key box code or a unit number: ``intents.yaml`` forbids
#: ``check_in_support`` from ever giving those out via ``answer_from_knowledge``, verified or not
#: — that disclosure belongs to ``access_code_request``'s own tool (``lookup_reservation``,
#: roadmap 3.1, unimplemented), gated on a real booking reference rather than a bool. A wifi
#: password is a fact this tool may legitimately hold and gate on ``identity_verified``.
_WIFI_PASSWORD = Fact(
    id="1",
    topic="wifi",
    question="what's the wifi password",
    answer="Flex2026",
    sensitive=True,
)


def test_a_sensitive_match_is_withheld_from_an_unverified_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1 is not built yet (roadmap track G) — until it is, a sensitive fact is never disclosed."""
    monkeypatch.setitem(
        REGISTRY, "answer_from_knowledge", AnswerFromKnowledge(_FakeKnowledge([_WIFI_PASSWORD]))
    )
    action, task = asyncio.run(
        handle(
            _turn("what's the wifi password"),
            "property_question",
            0.95,
            {},
            None,
            identity_verified=False,
        )
    )
    assert action.kind == "handoff"
    assert task.status == TaskStatus.HANDED_OFF


def test_a_sensitive_match_is_said_to_a_verified_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        REGISTRY, "answer_from_knowledge", AnswerFromKnowledge(_FakeKnowledge([_WIFI_PASSWORD]))
    )
    action, task = asyncio.run(
        handle(
            _turn("what's the wifi password"),
            "property_question",
            0.95,
            {},
            None,
            identity_verified=True,
        )
    )
    assert action.kind == "say"
    assert action.text == "Flex2026"


def test_new_intent_creates_new_task() -> None:
    old_task = Task(intent="directions")
    action, task = asyncio.run(
        handle(
            _turn("check availability"),
            "availability_check",
            0.95,
            {},
            old_task,
        )
    )
    assert task.intent == "availability_check"
    assert action.kind == "ask"


def test_a_task_that_has_asked_enough_times_fetches_a_person() -> None:
    """``defaults.max_clarifying_turns``, honoured at last (roadmap A5).

    The vocabulary has declared this since item 0.3 and nothing read it, which was harmless while
    every message opened a fresh task. Once a task survives between messages, a task that cannot
    be filled asks the same question forever — so the budget is what stops a receptionist looping
    at a guest who is not answering the question.
    """
    task = Task(intent="availability_check")
    action, updated = asyncio.run(
        handle(_turn(), "availability_check", 0.95, {}, task, turns_taken=3)
    )

    assert action.kind == "handoff"
    assert updated.status == TaskStatus.HANDED_OFF


def test_the_budget_does_not_cut_a_task_off_that_is_ready_to_act() -> None:
    """The guard is about questions, not about the job.

    A task with everything it needs executes even on the last turn of the budget; handing off a
    request we could simply have completed would be worse than the loop it prevents.
    """
    task = Task(
        intent="availability_check",
        slots={"check_in": "4 June", "check_out": "6 June", "guests": "2", "unit": "A1"},
        confirmed={"check_in", "check_out", "guests", "unit"},
    )
    action, updated = asyncio.run(
        handle(_turn(), "availability_check", 0.95, {}, task, turns_taken=9)
    )

    assert action.kind == "say"
    assert updated.status == TaskStatus.COMPLETED


def test_a_turn_within_budget_still_asks() -> None:
    action, _task = asyncio.run(
        handle(
            _turn(),
            "availability_check",
            0.95,
            {},
            Task(intent="availability_check"),
            turns_taken=1,
        )
    )
    assert action.kind == "ask"
