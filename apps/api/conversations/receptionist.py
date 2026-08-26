"""The receptionist — the composer that drives tasks within a conversation.

Ported from the v2 scaffold with one key change: instead of accepting a scaffold-specific
``Understanding`` type, this accepts intent/confidence/extracted_slots directly so it works
with the existing classification pipeline.

**``turns_taken`` is A5's addition, and it is a safety rail rather than a feature.** Once a task
survives between messages, a task that cannot make progress no longer fails — it loops, asking a
guest the same question every time they reply. The vocabulary has always declared
``defaults.max_clarifying_turns`` and ``defaults.on_max_turns`` and nothing has ever read them;
they are read here, because this is the file that decides what to say next. A receptionist that
has asked three times and learned nothing fetches a person.

**What 2.4 changed, and what it deliberately left alone.** Every intent that reached ``execute``
used to get the same reply — "All set! I've noted everything down." — whether or not that was
true. For the five intents whose ``terminal_tool`` is ``answer_from_knowledge`` that was a
standing lie: "is there parking?" was never looked up anywhere. ``_execute`` now actually calls
the tool the vocabulary names for that one case and hands off on a real "I don't know"
(``defaults.on_no_knowledge``). Every other ``terminal_tool`` (``check_availability``,
``lookup_reservation``, ``quote_price``, ``hold_slot``, ``confirm_booking``, ``create_ticket``) has
no implementation yet — that is roadmap 3.1, not 2.4 — and keeps the old placeholder reply rather
than being silently widened into a promise this item does not keep.
"""

from __future__ import annotations

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.conversations.task import Task, TaskStatus
from apps.api.conversations.tools import REGISTRY
from apps.api.core.autonomy import Autonomy, decide_autonomy
from apps.api.schemas.envelope import InboundTurn, OutboundAction

HANDOFF_TEXT = "Let me connect you with someone who can help."


#: Terminal tools this file actually runs. The rest (``check_availability``, ``quote_price``,
#: ``hold_slot``, ``confirm_booking``, ``lookup_appointment``, ``lookup_reservation``,
#: ``create_ticket``) are not built yet, and an intent that names one hands off rather than
#: claiming success — see ``_UNBUILT_TEXT``.
_KNOWLEDGE_TOOL = "answer_from_knowledge"

#: Tools that produce their reply directly from ``human_summary`` and cannot fail. ``greet`` and
#: ``close_conversation`` are the two ends of a conversation, and neither has anything to look up.
_DIRECT_TOOLS = frozenset({"greet", "close_conversation"})

#: What an unbuilt terminal tool says. **This wording is the point of the whole branch.**
#:
#: Every unimplemented tool used to fall through to "All set! I've noted everything down." — a
#: sentence that tells someone who just asked to book an appointment that they have one. Nothing
#: was written anywhere. On a booking journey that is not a rough edge, it is the receptionist
#: lying about the one fact the customer came for, and they arrive at a clinic that has never
#: heard of them. An unbuilt capability is a hand-off, and it says so.
_UNBUILT_TEXT = "Let me check that with the team and come straight back to you."


async def _hand_off(task: Task, tool_name: str) -> tuple[OutboundAction, Task]:
    """Mark the task handed off, run whichever tool the vocabulary names, and say so.

    The tool is looked up by name rather than hardcoded because ``on_max_turns`` and the
    autonomy ceiling both point at one by name in ``intents.yaml``. A name the registry does not
    know is not worth crashing a live conversation over — the guest still gets the reply and the
    task is still marked handed off, which is what actually fetches a person.
    """
    task.status = TaskStatus.HANDED_OFF
    tool = REGISTRY.get(tool_name)
    if tool is not None:
        await tool.run()
    return OutboundAction(kind="handoff", text=HANDOFF_TEXT), task


async def handle(
    turn: InboundTurn,
    intent: str,
    confidence: float,
    extracted_slots: dict[str, str],
    task: Task | None,
    *,
    identity_verified: bool = False,
    emergency: bool = False,
    turns_taken: int = 0,
    vocabulary: Vocabulary | None = None,
) -> tuple[OutboundAction, Task]:
    """Process one turn and return the action to take plus the updated task state.

    This is the core receptionist loop: check autonomy, manage task state, decide next step.
    """
    vocab = vocabulary or default_vocabulary()

    if task is None or task.intent != intent:
        task = Task(intent=intent, vocabulary=vocab)

    task.absorb(extracted_slots)

    autonomy: Autonomy = decide_autonomy(
        intent,
        confidence,
        identity_verified=identity_verified,
        emergency=emergency,
        vocabulary=vocab,
    )

    if autonomy == "hand_off":
        return await _hand_off(task, "handoff_to_human")

    step, slot = task.next_step()

    # Asking again is only worth doing while it is still making progress. `turns_taken` counts
    # what we have already said on this task, so the guard fires on the reply *after* the limit
    # rather than on the last useful question.
    if step in ("ask", "confirm") and turns_taken >= vocab.defaults.max_clarifying_turns:
        return await _hand_off(task, vocab.defaults.on_max_turns)

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

    intent_def = next((i for i in vocab.intents if i.name == intent), None)
    tool_name = intent_def.terminal_tool if intent_def is not None else None

    if tool_name == _KNOWLEDGE_TOOL:
        return await _answer_from_knowledge(task, turn, identity_verified, vocab)

    if tool_name in _DIRECT_TOOLS:
        return await _run_direct(task, tool_name, extracted_slots)

    if tool_name == "take_message":
        take_message = REGISTRY.get("take_message")
        if take_message is not None:
            await take_message.run()
        task.status = TaskStatus.COMPLETED
        return OutboundAction(kind="say", text="Thanks — I've noted that down."), task

    # An unbuilt terminal tool. Record the message so nothing is lost, then fetch a person.
    # Deliberately *not* a success reply: see ``_UNBUILT_TEXT``.
    take_message = REGISTRY.get("take_message")
    if take_message is not None:
        await take_message.run()
    task.status = TaskStatus.HANDED_OFF
    return OutboundAction(kind="handoff", text=_UNBUILT_TEXT), task


async def _run_direct(
    task: Task, tool_name: str, extracted_slots: dict[str, str]
) -> tuple[OutboundAction, Task]:
    """Run a tool whose reply is its ``human_summary`` and which has nothing to look up.

    ``customer_name`` is passed through rather than read from the task's slots because a greeting
    declares it optional and the name comes from the channel profile, not from anything the
    customer typed.
    """
    tool = REGISTRY.get(tool_name)
    if tool is None:  # A vocabulary naming a tool nobody registered. Not worth a live crash.
        task.status = TaskStatus.HANDED_OFF
        return OutboundAction(kind="handoff", text=HANDOFF_TEXT), task

    result = await tool.run(customer_name=extracted_slots.get("customer_name"))
    task.status = TaskStatus.COMPLETED
    return OutboundAction(kind="say", text=result.human_summary or ""), task


async def _answer_from_knowledge(
    task: Task, turn: InboundTurn, identity_verified: bool, vocab: Vocabulary
) -> tuple[OutboundAction, Task]:
    """Run the knowledge lookup (roadmap 2.4) and say what it found, or fetch a person.

    ``turn.text`` rather than a ``topic`` slot: ``property_question`` declares one, but slot
    extraction (item 2.x) does not exist, so ``extracted_slots`` is always ``{}`` and the raw
    message is the only signal the tool has to match against.
    """
    tool = REGISTRY.get(_KNOWLEDGE_TOOL)
    result = (
        await tool.run(
            tenant_id=str(turn.tenant_id),
            question=turn.text or "",
            identity_verified=identity_verified,
        )
        if tool is not None
        else None
    )

    if result is None or not result.ok:
        return await _hand_off(task, vocab.defaults.on_no_knowledge)

    task.status = TaskStatus.COMPLETED
    return OutboundAction(kind="say", text=result.human_summary or ""), task
