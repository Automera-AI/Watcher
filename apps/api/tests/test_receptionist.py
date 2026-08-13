"""Tests for the receptionist function (Item 2.2)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from apps.api.conversations.receptionist import handle
from apps.api.conversations.task import Task, TaskStatus
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


def test_no_required_slots_executes_directly() -> None:
    action, task = asyncio.run(handle(_turn("how do I get there"), "directions", 0.95, {}, None))
    assert action.kind == "say"
    assert task.status == TaskStatus.COMPLETED


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
