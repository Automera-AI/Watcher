"""The receptionist — the composer that drives tasks within a conversation.

Ported from the v2 scaffold with one key change: instead of accepting a scaffold-specific
``Understanding`` type, this accepts intent/confidence/extracted_slots directly so it works
with the existing classification pipeline.
"""

from __future__ import annotations

from apps.api.conversations.task import Task, TaskStatus
from apps.api.conversations.tools import REGISTRY
from apps.api.core.autonomy import Autonomy, decide_autonomy
from apps.api.schemas.envelope import InboundTurn, OutboundAction


async def handle(
    turn: InboundTurn,
    intent: str,
    confidence: float,
    extracted_slots: dict[str, str],
    task: Task | None,
    *,
    identity_verified: bool = False,
    emergency: bool = False,
) -> tuple[OutboundAction, Task]:
    """Process one turn and return the action to take plus the updated task state.

    This is the core receptionist loop: check autonomy, manage task state, decide next step.
    """
    if task is None or task.intent != intent:
        task = Task(intent=intent)

    task.absorb(extracted_slots)

    autonomy: Autonomy = decide_autonomy(
        intent,
        confidence,
        identity_verified=identity_verified,
        emergency=emergency,
    )

    if autonomy == "hand_off":
        task.status = TaskStatus.HANDED_OFF
        handoff_tool = REGISTRY.get("handoff_to_human")
        if handoff_tool is not None:
            await handoff_tool.run()
        return (
            OutboundAction(
                kind="handoff",
                text="Let me connect you with someone who can help.",
            ),
            task,
        )

    step, slot = task.next_step()

    if step == "ask":
        assert slot is not None
        return (
            OutboundAction(
                kind="ask",
                text=f"Could you please provide the {slot.replace('_', ' ')}?",
            ),
            task,
        )

    if step == "confirm":
        assert slot is not None
        value = task.slots[slot]
        return (
            OutboundAction(
                kind="confirm",
                text=f"Just to confirm: {slot.replace('_', ' ')} is {value}?",
                quick_replies=["Yes", "No"],
            ),
            task,
        )

    task.status = TaskStatus.EXECUTING
    take_message = REGISTRY.get("take_message")
    if take_message is not None:
        await take_message.run()
    task.status = TaskStatus.COMPLETED

    return (
        OutboundAction(
            kind="say",
            text="All set! I've noted everything down.",
        ),
        task,
    )
