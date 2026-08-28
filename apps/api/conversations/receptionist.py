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
(``defaults.on_no_knowledge``).

**What demo step 6 added: the booking journey, and the two things that made it unreachable.**

``check_availability``, ``quote_price``, ``hold_slot`` and ``confirm_booking`` are wired here now,
against the imported catalogue. Where they are *not* registered — the holiday-home vertical, or a
process that never called ``configure_clinic`` — nothing changes: the name is absent, and an
unbuilt terminal tool is still a hand-off rather than a claim of success.

Two things had to be fixed for any of it to be reachable, and neither was in the tools.

*Nothing ever agreed to anything.* ``Task.confirmed`` was only ever emptied, so an intent
declaring ``confirm_before_acting`` read a detail back and read it back again until the
clarifying-turn limit fetched a person. ``Task.agree`` and ``conversations/confirmation.py`` close
that loop, and the read-back now covers everything outstanding in one message rather than one
detail per turn — three separate confirmations do not fit inside ``max_clarifying_turns: 2``.

*And "تمام" ended the conversation.* Classified flat it is ``thanks_closing``, which is the right
label most of the time and the wrong one by one word mid-booking: the task would be abandoned and
the receptionist would say goodbye to somebody who was about to have an appointment. A short reply
is now read against the read-back that is actually outstanding, *before* the classified intent is
allowed to switch tasks. That is the clinic vocabulary's dialogue-state rule, in the one place the
demo needs it.

**What demo step 7 added: the clinical gate.** A receptionist that can write an appointment can
write one for a filler injection into a pregnant patient and confirm it with a reference number.
``core/screening.py`` decides what stops a booking; this file is where it is asked, on every turn
of a booking task and again once the treatment's category is known. A block hands off and says
nothing else — no reassurance, no follow-up question, because asking one is a medical-history
interview conducted in order to decide.

The remaining ``terminal_tool`` values (``lookup_reservation``, ``lookup_appointment``,
``create_ticket``) still have no implementation, and still hand off.
"""

from __future__ import annotations

from datetime import date

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.conversations.confirmation import reads_as_no, reads_as_yes
from apps.api.conversations.slots import normalise_slots
from apps.api.conversations.task import Task, TaskStatus
from apps.api.conversations.tools import REGISTRY, ToolResult, current_copy, fill_template
from apps.api.core.autonomy import Autonomy, decide_autonomy
from apps.api.core.screening import ScreeningBlock, screen
from apps.api.schemas.common import HIGH_CONFIDENCE_THRESHOLD
from apps.api.schemas.enums import IntentType
from apps.api.schemas.envelope import InboundTurn, OutboundAction

HANDOFF_TEXT = "Let me connect you with someone who can help."


#: Terminal tools this file runs by name. ``lookup_reservation``, ``lookup_appointment`` and
#: ``create_ticket`` are still unbuilt, and an intent that names one hands off rather than claiming
#: success — see ``_UNBUILT_TEXT``.
_KNOWLEDGE_TOOL = "answer_from_knowledge"
_AVAILABILITY_TOOL = "check_availability"
_QUOTE_TOOL = "quote_price"
_HOLD_TOOL = "hold_slot"
_BOOKING_TOOL = "confirm_booking"

#: The slot a booking is offered *into* rather than asked for. When it is the only thing missing,
#: the receptionist does not ask an open question — it calls ``check_availability`` and offers what
#: the diary holds, which is the only way the vocabulary's "never offer a slot the scheduling
#: system did not return" can be kept while still collecting a time.
_TIME_SLOT = "requested_time"

#: Where the durable booking reference is kept once one exists. Not a vocabulary slot — nothing
#: extracts it from a message — but it lives with the task because that is what survives to the
#: closing turn, which is the only place it is read (``CloseConversation``).
_REFERENCE_SLOT = "booking_reference"

#: Said when an appointment has actually been written, and only then. The reference is part of the
#: sentence rather than an afterthought: it is the thing that makes the claim checkable, and a
#: confirmation without one is the "All set!" bug wearing a better sentence.
#:
#: The tenant may replace it (``ConversationCopy.booking_confirmed``) and most will, because this
#: sentence and the read-back are the only two the receptionist composes itself — every other
#: word a patient reads already came from configuration, and until the journey was run end to end
#: nobody had noticed that the two turns at the centre of the demo were still in English.
_BOOKED_TEXT = "That's booked. Your reference is {booking_reference}"

#: The read-back. ``{details}`` carries the labels the default reads with; ``{values}`` is the
#: same list without them, for a template in a language those English labels do not belong in.
_READ_BACK_TEXT = "Just to confirm: {details}?"

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

#: What the classifier says when it cannot tell what a message is. A mid-conversation fragment is
#: the ordinary case: "الساعة ٧" carries no request, no treatment and no verb, and out of context
#: it is not a booking — it is two words. Both tiers label it ``unclear`` and the escalation model
#: is, if anything, more certain of that than the cheap one.
_UNCLEAR = IntentType.UNCLEAR.value


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
    conversation_id: str | None = None,
    today: date | None = None,
) -> tuple[OutboundAction, Task]:
    """Process one turn and return the action to take plus the updated task state.

    This is the core receptionist loop: check autonomy, manage task state, decide next step.
    """
    vocab = vocabulary or default_vocabulary()

    # The dialogue-state rule: a reply is read against the question that is genuinely outstanding
    # *before* the classified intent is allowed to start a different task. "تمام" is
    # `thanks_closing` on its own and it is `yes, book it` after a read-back; "الساعة ٧" is two
    # words on its own and it is the appointment time after an offer. The difference is never in
    # the message — it is in what the conversation is waiting for.
    #
    # Two shapes, and the second is what running the journeys on real classifications forced.
    # A read-back takes a yes or a no. An outstanding *question* takes a value, and the model
    # cannot supply one because it does not know what was asked: it sees a fragment and says
    # `unclear`, which switches tasks and fetches a person one turn after the patient was offered
    # a time. So an `unclear` turn is offered to the slot the task is actually waiting on, and
    # only a message that resolves into that slot is treated as an answer.
    answering = task is not None and task.awaiting_agreement and _is_an_answer(turn.text)
    supplied: dict[str, str] = {}
    if not answering and task is not None and intent == _UNCLEAR:
        supplied = _read_as_answer(task, turn, vocab, today)

    if answering or supplied:
        assert task is not None
        intent = task.intent
        # **And the confidence with it.** `decide_autonomy` gates on how sure the *model* was, and
        # the model was not asked this question: the intent came from the conversation's own
        # state, which is a fact rather than a guess. Leaving the model's 0.3 in `unclear` to gate
        # a booking it never labelled is how the read-back rule ends in the hand-off it exists to
        # prevent — which is exactly what it did, silently, until the journeys ran on real
        # classifications. Nothing else is relaxed: the clinical gate below reads every turn, and
        # a booking still reaches `confirm_booking` only through a read-back the patient agreed to.
        confidence = max(confidence, HIGH_CONFIDENCE_THRESHOLD)

    if task is None or task.intent != intent:
        task = Task(intent=intent, vocabulary=vocab)

    task.absorb({**extracted_slots, **supplied})

    if answering:
        if reads_as_yes(turn.text or ""):
            task.agree()
        else:
            # A refusal. Nothing is agreed and nothing is guessed about which detail was wrong;
            # the customer says so in their own words on the next turn, and `absorb` drops the
            # confirmation of anything they change. Two of these and `on_max_turns` fetches a
            # person, which is the right end for a read-back that keeps being rejected.
            return (
                OutboundAction(
                    kind="ask",
                    text="Sorry — which detail should I change?",
                ),
                task,
            )

    autonomy: Autonomy = decide_autonomy(
        intent,
        confidence,
        identity_verified=identity_verified,
        emergency=emergency,
        vocabulary=vocab,
    )

    if autonomy == "hand_off":
        return await _hand_off(task, "handoff_to_human")

    # The clinical gate (demo step 7), on what the patient just said. Checked on every turn of a
    # booking rather than once at the start: a disclosure arrives when the patient thinks of it,
    # which is usually while they are answering a question about something else. After `absorb`,
    # so what they told us is kept for the person who takes the conversation over.
    if _is_booking(intent, vocab) and (block := screen(turn.text, vocabulary=vocab)) is not None:
        return await _blocked(task, block)

    step, slot = task.next_step()

    # Asking again is only worth doing while it is still making progress. `turns_taken` counts
    # what we have already said on this task, so the guard fires on the reply *after* the limit
    # rather than on the last useful question.
    if step in ("ask", "confirm") and turns_taken >= vocab.defaults.max_clarifying_turns:
        return await _hand_off(task, vocab.defaults.on_max_turns)

    intent_def = next((i for i in vocab.intents if i.name == intent), None)
    tool_name = intent_def.terminal_tool if intent_def is not None else None

    if step == "ask":
        assert slot is not None
        if slot == _TIME_SLOT and tool_name == _BOOKING_TOOL:
            # Not an open question. The one detail still missing is *which* appointment, and the
            # only honest way to collect it is to offer what the diary actually holds.
            return await _offer_times(task, turn, conversation_id, vocab)
        return (
            OutboundAction(
                kind="ask",
                text=f"Could you please provide the {slot.replace('_', ' ')}?",
            ),
            task,
        )

    if step == "confirm":
        return await _read_back(task, turn, conversation_id, booking=tool_name == _BOOKING_TOOL)

    task.status = TaskStatus.EXECUTING

    if tool_name in (_AVAILABILITY_TOOL, _QUOTE_TOOL):
        return await _answer_from_catalogue(task, turn, tool_name, conversation_id, vocab)

    if tool_name == _BOOKING_TOOL:
        return await _book(task, turn, conversation_id, vocab)

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

    ``customer_name`` is read from ``extracted_slots`` first because it comes from the channel
    profile rather than from anything the customer typed, and the task may never have collected it.

    ``booking_reference`` decides whether a closing may say an appointment was confirmed, so it is
    read from the task the conversation has been building rather than from this one message —
    the customer says "شكراً", not their reference. It is absent until ``confirm_booking`` exists
    to put one there, which is exactly why the confirmed-booking wording cannot be reached yet.
    """
    tool = REGISTRY.get(tool_name)
    if tool is None:  # A vocabulary naming a tool nobody registered. Not worth a live crash.
        task.status = TaskStatus.HANDED_OFF
        return OutboundAction(kind="handoff", text=HANDOFF_TEXT), task

    result = await tool.run(
        customer_name=extracted_slots.get("customer_name") or task.slots.get("customer_name"),
        booking_reference=task.slots.get("booking_reference"),
    )
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


def _is_an_answer(text: str | None) -> bool:
    """Whether a message is a short yes or no rather than a new request."""
    return reads_as_yes(text or "") or reads_as_no(text or "")


def _read_as_answer(
    task: Task, turn: InboundTurn, vocab: Vocabulary, today: date | None
) -> dict[str, str]:
    """The message read as a value for the slot the task is waiting on, or nothing.

    Deliberately narrow in three ways. It runs only when the classifier said ``unclear``, so a
    message the model *did* understand as something else is still a change of subject. It offers
    the message to one slot — the one the task's own next step is asking for — rather than
    guessing which detail it might be. And it goes through ``normalise_slots``, the same
    resolution the worker applies to the model's output, so "الساعة ٧" becomes 19:00 by the
    tenant's own rule and a value that resolves to nothing stays nothing.

    ``today`` is the tenant's date, passed in because this module has no clock and no timezone;
    it only matters for a date-valued slot, and the caller that has a tenant zone is the worker.
    """
    step, slot = task.next_step()
    if step != "ask" or slot is None or not turn.text:
        return {}
    return normalise_slots(
        task.intent,
        {slot: turn.text},
        vocabulary=vocab,
        today=today or turn.received_at.date(),
    )


def _readable(task: Task, slot: str) -> str:
    """One slot's value as a person would say it back.

    Only the date needs it: it is stored as ``2026-09-02`` because that is what a booking can act
    on, and reading an ISO date back to a patient asks them to confirm a string rather than a day.
    """
    value = task.slots.get(slot, "")
    if slot != "requested_date":
        return value
    try:
        return date.fromisoformat(value).strftime("%A %d %B")
    except ValueError:
        return value


async def _availability(
    task: Task, turn: InboundTurn, conversation_id: str | None
) -> ToolResult | None:
    """Ask the scheduling system what is free for this task, or ``None`` if it is not wired."""
    tool = REGISTRY.get(_AVAILABILITY_TOOL)
    if tool is None:
        return None
    return await tool.run(
        tenant_id=str(turn.tenant_id),
        conversation_id=conversation_id,
        service=task.slots.get("service"),
        branch=task.slots.get("branch"),
        requested_date=task.slots.get("requested_date"),
        session_count=task.slots.get("session_count"),
    )


def _slot_at(result: ToolResult, wanted_time: str) -> str | None:
    """The slot id whose start time is ``wanted_time``, out of what was just returned.

    Matched against *this* call's answer and never a remembered one. The diary moves between
    turns, and a slot id carried across from the message before it is a promise about a state of
    the world that has already changed — which is what ``quoting.max_age_seconds`` is about.
    """
    data = result.data or {}
    times = data.get("times") or []
    slot_ids = data.get("slot_ids") or []
    for offered, slot_id in zip(times, slot_ids, strict=False):
        if offered == wanted_time:
            return str(slot_id)
    return None


async def _offer_times(
    task: Task, turn: InboundTurn, conversation_id: str | None, vocab: Vocabulary
) -> tuple[OutboundAction, Task]:
    """Collect the appointment time by offering the ones that exist.

    A failure here is not a hand-off by default: "there is nothing free on Wednesday" is a real
    answer to a real question, and the patient's next move is another day. Only a tool that could
    not say anything at all — unwired, or a service name that reached nothing — fetches a person.
    """
    result = await _availability(task, turn, conversation_id)
    if result is None:
        return await _hand_off(task, "handoff_to_human")
    if (block := _screen_category(result, vocab)) is not None:
        return await _blocked(task, block)
    if result.human_summary:
        return OutboundAction(kind="ask", text=result.human_summary), task
    return await _hand_off(task, "handoff_to_human")


def _screen_category(result: ToolResult, vocab: Vocabulary) -> ScreeningBlock | None:
    """The clinical gate on the *treatment*, once the catalogue has said which one it is.

    Deliberately not run on the patient's words for the service — "عايزة فيلر" resolving to
    ``Filler`` is the catalogue's answer, and the gate reads the category the catalogue returned
    rather than guessing from the message. A booking whose service never resolved has no category
    to screen, and never reaches a confirmation either.
    """
    category = (result.data or {}).get("service_category")
    if not isinstance(category, str):
        return None
    return screen(None, service_category=category, vocabulary=vocab)


async def _read_back(
    task: Task,
    turn: InboundTurn,
    conversation_id: str | None,
    *,
    booking: bool,
) -> tuple[OutboundAction, Task]:
    """Read every outstanding detail back at once, and hold the slot while it is being answered.

    One message rather than one per detail. ``max_clarifying_turns`` is 2 and a clinic booking has
    four confirmable details, so a confirmation per turn cannot finish — the third read-back is the
    hand-off. Reading them back together is also simply what a receptionist does.

    The hold is placed *here*, at the read-back, and deliberately not at the offer: holding
    everything a browsing patient was shown would take an afternoon out of the diary, while holding
    nothing means the slot somebody is in the middle of agreeing to can be given away underneath
    them. A hold that fails is not fatal — the confirmation itself is atomic — so the patient is
    still asked, and finds out at the last moment rather than the first.
    """
    if booking:
        await _hold_for(task, turn, conversation_id)

    outstanding = tuple(task.unconfirmed)
    details = ", ".join(f"{slot.replace('_', ' ')} {_readable(task, slot)}" for slot in outstanding)
    values = "، ".join(_readable(task, slot) for slot in outstanding)
    template = current_copy().confirm_read_back or _READ_BACK_TEXT
    return (
        OutboundAction(
            kind="confirm",
            text=fill_template(template, details=details, values=values)
            or _READ_BACK_TEXT.format(details=details),
            quick_replies=["Yes", "No"],
        ),
        task,
    )


async def _hold_for(task: Task, turn: InboundTurn, conversation_id: str | None) -> None:
    """Reserve the slot this task is about to read back, if it can be identified and held."""
    hold = REGISTRY.get(_HOLD_TOOL)
    wanted_time = task.slots.get(_TIME_SLOT)
    if hold is None or conversation_id is None or not wanted_time:
        return
    result = await _availability(task, turn, conversation_id)
    slot_id = _slot_at(result, wanted_time) if result is not None else None
    if slot_id is None:
        return
    await hold.run(
        tenant_id=str(turn.tenant_id),
        conversation_id=conversation_id,
        slot_external_id=slot_id,
    )


async def _answer_from_catalogue(
    task: Task,
    turn: InboundTurn,
    tool_name: str,
    conversation_id: str | None,
    vocab: Vocabulary,
) -> tuple[OutboundAction, Task]:
    """Run ``check_availability`` or ``quote_price`` and say what came back.

    An unresolved or ambiguous service is not a failure to be hidden: the tool composes the
    question ("Which did you mean: Basic Facial / Facial?") and it is asked, because picking one
    would quote a real price for a treatment nobody asked about. A tool that came back with
    nothing to say at all falls through to ``on_tool_failure``, which fetches a person — the
    vocabulary's own answer, and the only safe one when a quote cannot be sourced.
    """
    tool = REGISTRY.get(tool_name)
    if tool is None:
        return await _unbuilt(task)

    result = await tool.run(
        tenant_id=str(turn.tenant_id),
        conversation_id=conversation_id,
        service=task.slots.get("service"),
        branch=task.slots.get("branch"),
        requested_date=task.slots.get("requested_date"),
        requested_time=task.slots.get(_TIME_SLOT),
        # The quantity the patient said, which is what tells three same-named packages apart.
        session_count=task.slots.get("session_count"),
    )
    if result.human_summary:
        task.status = TaskStatus.COMPLETED if result.ok else TaskStatus.COLLECTING
        return OutboundAction(kind="say" if result.ok else "ask", text=result.human_summary), task
    return await _hand_off(task, vocab.defaults.on_tool_failure)


async def _book(
    task: Task, turn: InboundTurn, conversation_id: str | None, vocab: Vocabulary
) -> tuple[OutboundAction, Task]:
    """Create the appointment the patient has just agreed to, and give them its reference.

    **The reference goes into the task**, which is the whole reason ``closing_booking_confirmed``
    exists and has never been reachable: the confirmed-booking closing renders only when a durable
    reference is there to put in it, and this is the one line in the system that puts one there.
    Everything before this point is a conversation; this is the first moment an appointment exists
    anywhere.

    The slot is looked up again rather than remembered. It was held at the read-back, and a hold is
    visible to the conversation that placed it, so the slot the patient agreed to is the slot they
    get — while anything that changed underneath in the meantime is seen now rather than assumed
    away.
    """
    tool = REGISTRY.get(_BOOKING_TOOL)
    wanted_time = task.slots.get(_TIME_SLOT)
    if tool is None or conversation_id is None or not wanted_time:
        return await _unbuilt(task)

    availability = await _availability(task, turn, conversation_id)
    if availability is not None and (block := _screen_category(availability, vocab)) is not None:
        # Belt and braces on the last line before an appointment is written. The category was
        # already screened when the times were offered, but a catalogue can be re-imported between
        # two messages, and this is the check whose absence writes the appointment.
        return await _blocked(task, block)
    slot_id = _slot_at(availability, wanted_time) if availability is not None else None
    if slot_id is None:
        # The time is no longer on offer. Say what is, rather than booking something else.
        task.slots.pop(_TIME_SLOT, None)
        task.confirmed.discard(_TIME_SLOT)
        return await _offer_times(task, turn, conversation_id, vocab)

    result = await tool.run(
        tenant_id=str(turn.tenant_id),
        conversation_id=conversation_id,
        slot_external_id=slot_id,
        customer_name=task.slots.get("customer_name"),
        phone=task.slots.get("phone"),
    )
    reference = (result.data or {}).get("booking_reference") if result.ok else None
    if not result.ok or not reference:
        if result.human_summary:
            task.status = TaskStatus.COLLECTING
            return OutboundAction(kind="say", text=result.human_summary), task
        return await _hand_off(task, vocab.defaults.on_tool_failure)

    task.slots[_REFERENCE_SLOT] = str(reference)
    task.status = TaskStatus.COMPLETED
    template = current_copy().booking_confirmed or _BOOKED_TEXT
    return (
        OutboundAction(
            kind="say",
            text=fill_template(template, booking_reference=str(reference))
            or _BOOKED_TEXT.format(booking_reference=reference),
        ),
        task,
    )


async def _unbuilt(task: Task) -> tuple[OutboundAction, Task]:
    """Record the message and fetch a person. Never a success reply — see ``_UNBUILT_TEXT``."""
    take_message = REGISTRY.get("take_message")
    if take_message is not None:
        await take_message.run()
    task.status = TaskStatus.HANDED_OFF
    return OutboundAction(kind="handoff", text=_UNBUILT_TEXT), task


def _is_booking(intent: str, vocab: Vocabulary) -> bool:
    """Whether this intent ends in an appointment being written."""
    found = next((i for i in vocab.intents if i.name == intent), None)
    return found is not None and found.terminal_tool == _BOOKING_TOOL


async def _blocked(task: Task, block: ScreeningBlock) -> tuple[OutboundAction, Task]:
    """Stop, and fetch the person whose decision this is.

    Nothing is said about *why*. A reply naming the disclosure — "since you're pregnant…" — is the
    receptionist stating a clinical fact about a patient, which is the line the whole vertical is
    drawn around; and a reply that asks a follow-up implies the answer could change the outcome.
    The hand-off wording is the ordinary one, and a clinician says the rest.

    The block's own ``action`` is used rather than a hardcoded name, so a vocabulary that routes
    screening somewhere other than the general hand-off queue is obeyed rather than overridden.
    """
    return await _hand_off(task, block.action)
