"""The receptionist itself.

Deliberately boring. It receives a normalised turn, works out what is going on, decides how much
it is allowed to do on its own, and returns what to say. It does not know what a phone is.
"""

from __future__ import annotations

import structlog

from app.core.envelope import InboundTurn, OutboundAction
from app.core.task import Task, TaskStatus
from app.core.understanding import Understanding, decide_autonomy
from app.tools.registry import REGISTRY

log = structlog.get_logger()

FRIENDLY_SLOT_NAMES = {
    "check_in": "the date you would like to arrive",
    "check_out": "the date you are leaving",
    "guests": "how many people are staying",
    "unit_type": "what size apartment you need",
    "reservation_ref": "your booking reference",
    "unit": "which apartment",
    "issue_description": "what the problem is",
    "preferred_time": "when suits you",
    "contact_name": "your name",
    "change_requested": "what you would like to change",
}


async def handle(
    turn: InboundTurn,
    understanding: Understanding,
    task: Task | None,
    *,
    identity_verified: bool = False,
) -> tuple[OutboundAction, Task]:
    """Decide what to say back. Pure, so it is easy to test and easy to score."""

    if task is None or task.intent != understanding.intent:
        task = Task(intent=understanding.intent)
    task.absorb(understanding.slots)

    autonomy = decide_autonomy(understanding, identity_verified=identity_verified)
    log.info(
        "turn_handled",
        intent=understanding.intent,
        band=understanding.band,
        autonomy=autonomy,
        missing=task.missing,
    )

    if autonomy == "hand_off":
        task.status = TaskStatus.HANDED_OFF
        await REGISTRY["handoff_to_human"]().run(
            turn.tenant_id, turn.idempotency_key, reason=f"{understanding.intent}:{understanding.band}"
        )
        return (
            OutboundAction(
                kind="handoff",
                text="Let me put you through to a colleague who can help with that.",
            ),
            task,
        )

    step, slot = task.next_step()

    if step == "ask":
        return (
            OutboundAction(
                kind="ask",
                text=f"Happy to help. Could you tell me {FRIENDLY_SLOT_NAMES.get(slot, slot)}?",
            ),
            task,
        )

    if step == "confirm":
        return (
            OutboundAction(
                kind="confirm",
                text=f"Just to check I have this right, {FRIENDLY_SLOT_NAMES.get(slot, slot)} is "
                f"{task.slots[slot]}. Is that correct?",
                quick_replies=["Yes", "No"],
                requires_ack=True,
            ),
            task,
        )

    task.status = TaskStatus.READY
    return (
        OutboundAction(
            kind="say",
            text=understanding.reply_draft or "Thank you, I have everything I need.",
        ),
        task,
    )
