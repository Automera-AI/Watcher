"""The receptionist — the composer that drives tasks within a conversation.

Ported from the v2 scaffold with one key change: instead of accepting a scaffold-specific
``Understanding`` type, this accepts intent/confidence/extracted_slots directly so it works
with the existing classification pipeline.

**The clarification budget is a safety rail rather than a feature.** Once a task survives between
messages, a task that cannot make progress no longer fails — it loops, asking a guest the same
question every time they reply. A reserved counter now records consecutive non-progress turns;
real task facts and confirmations reset it. ``turns_taken`` remains only as the fallback for an
active task persisted before that counter existed. The vocabulary's
``defaults.max_clarifying_turns`` and ``defaults.on_max_turns`` are enforced here because this is
the file that decides what to say next.

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

from collections.abc import Callable
from datetime import UTC, date, datetime

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.conversations.confirmation import reads_as_no, reads_as_yes
from apps.api.conversations.renderer import render_reply
from apps.api.conversations.slots import normalise_slots, strip_unsupported_temporal_slots
from apps.api.conversations.task import (
    AWAITING_ANOTHER_DATE_SLOT,
    NON_PROGRESS_TURNS_SLOT,
    Task,
    TaskStatus,
    is_internal_slot,
)
from apps.api.conversations.tools import (
    REGISTRY,
    ConversationCopy,
    ToolResult,
    current_copy,
    fill_template,
    strip_unsupported_clinic_slots,
)
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

#: The treatment slot. When it is what is missing, the receptionist asks a contextual question in
#: the patient's language rather than the generic English slot prompt — the branch and day are
#: already known, so "which treatment, at Maadi, tomorrow?" is what a receptionist would ask. See
#: ``_ask_for_service``.
_SERVICE_SLOT = "service"

#: The intents whose missing ``service`` earns the booking-specific Arabic ask. Both flows end at
#: the diary — one offers what is free (``availability_check``), one books it (``booking_enquiry``)
#: — so "which treatment would you like to book, at Maadi, tomorrow?" is the right question. The
#: other intents that also require ``service`` are *not* booking anything: ``price_enquiry`` is a
#: quote and ``preparation_aftercare_info`` is a how-to, and asking either "which service would you
#: like to book?" is wrong. Those keep the generic slot prompt — the ask is gated on the flow, not
#: on the slot being ``service`` alone.
_SERVICE_ASK_INTENTS = frozenset(
    {IntentType.AVAILABILITY_CHECK.value, IntentType.BOOKING_ENQUIRY.value}
)

#: The intent a successful availability offer continues into. An ``availability_check`` that came
#: back with concrete, bookable times has answered the patient's question, but on the demo flow the
#: question is the first half of a booking: the patient's next message names one of the offered
#: times, and that reply is continued as this intent so the booking it belongs to can collect it.
#: See ``_answer_from_catalogue``.
_BOOKING_INTENT = IntentType.BOOKING_ENQUIRY.value

#: The intent transitions that continue the task in flight instead of resetting it. A change of
#: classified intent normally opens a fresh task — the guest asked about something else, and the
#: old job's slots do not belong to the new one. One directed pair is the exception: a patient who
#: asked "what's free tomorrow at Maadi?" (``availability_check``) and then names a treatment is on
#: the same booking journey, and the branch and day they already gave still belong to it. The
#: classifier relabels that second turn ``booking_enquiry`` — it now expresses an intent to book —
#: and resetting on the relabel throws the branch and day away and asks for them again, which is the
#: context loss this guard closes.
#:
#: Only this pair, and only in this direction. ``booking_enquiry`` requires everything
#: ``availability_check`` collects (service, branch, date) plus a time it *offers* rather than asks
#: for, so every slot carried forward is one the booking still needs — the transition is
#: superset-safe. This is not generic cross-intent merging: any other relabel
#: (``price_enquiry``, ``property_question``, a greeting, …) still starts clean and inherits
#: nothing, so nothing a patient said while booking leaks into an unrelated request.
_COMPATIBLE_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {(IntentType.AVAILABILITY_CHECK.value, IntentType.BOOKING_ENQUIRY.value)}
)

#: Where the durable booking reference is kept once one exists. Not a vocabulary slot — nothing
#: extracts it from a message — but it lives with the task because that is what survives to the
#: closing turn, which is the only place it is read (``CloseConversation``).
_REFERENCE_SLOT = "booking_reference"

#: The proof that a **concrete** availability offer was actually put in front of the patient, kept
#: with the task the same way ``booking_reference`` is: not a vocabulary slot (nothing extracts it,
#: and it is not ``required`` so it never reaches a read-back), but persisted in the task's own
#: slots — the smallest existing surface that survives a process restart with no new column or
#: migration. Its *presence* is what distinguishes a pending booking that is genuinely waiting on
#: offered times from an ordinary booking clarification that merely happens to be
#: ``booking_enquiry / COLLECTING`` with no time yet (a day with nothing free stays in that shape
#: but was never offered anything). Its *value* is the inbound turn's ``received_at`` at the moment
#: the offer was made — the event timestamp the freshness window is measured from, not the
#: wall-clock time the row was written. Both are only ever set where the availability result carries
#: real slots; see ``_mark_concrete_offer`` and ``resumed_offer_is_stale``.
_OFFER_AT_SLOT = "availability_offered_at"

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

#: The missing-service question, in Egyptian Arabic. Composed here rather than left to the generic
#: English slot prompt, because this is the one ask on the demo's booking flow and the branch and
#: day it should carry are already in the task. ``{branch}`` and ``{date}`` are pre-composed
#: fragments — "في فرع المعادي", "بكرة", or empty — so the sentence reads whether the turn has
#: both, one, or neither. The tenant may replace it (``ConversationCopy.ask_service``); the default
#: is Arabic in code so no configuration is required for the patient to be asked in their language.
_ASK_SERVICE_TEXT = "أكيد، تحبي تحجزي أنهي خدمة{branch}{date}؟"

#: The other three booking asks, in Egyptian Arabic. ``service`` earns a contextual sentence because
#: it carries the branch and day already held; ``branch``, ``requested_date`` and ``requested_time``
#: are single questions and do not. They are the two turns the step-by-step journey used to answer
#: in English — ``برايم ليز`` is followed by a branch ask and then a date ask — and, like
#: ``_ASK_SERVICE_TEXT``, the default is Arabic in code so the demo needs no configuration to ask in
#: the patient's language. Keyed by slot rather than intent: these three slots exist only on the
#: clinic vocabulary (a holiday-home booking asks ``check_in``/``unit_type``, never ``branch``), so
#: an Arabic default keyed to them cannot reach another vertical's booking. A tenant overrides each
#: through ``ConversationCopy.ask_branch`` / ``ask_date`` / ``ask_time``. No placeholders.
_ASK_BRANCH_TEXT = "تمام، تحبي تحجزي في أنهي فرع؟"
_ASK_DATE_TEXT = "تمام، تحبي الحجز يكون يوم ايه؟"
_ASK_TIME_TEXT = "تمام، تحبي الميعاد الساعة كام؟"

#: The clinic booking slots whose missing-value question is asked in Arabic, mapped to their default
#: wording and the ``ConversationCopy`` field a tenant overrides them with. ``requested_time`` is
#: here for completeness — the demo *offers* a time rather than asking for one — so its fallback is
#: Arabic on the rare turn it is reached directly. See ``_ask_for_slot``.
_CLINIC_SLOT_ASKS: dict[str, str] = {
    "branch": _ASK_BRANCH_TEXT,
    "requested_date": _ASK_DATE_TEXT,
    _TIME_SLOT: _ASK_TIME_TEXT,
}

#: Asked after a patient declines a read-back: which detail to change. Reached on the booking
#: journey (a "لأ" to the confirmation), so a clinic wants it in Arabic — but it is also reached
#: by a holiday-home read-back, so the in-code default stays neutral English and a clinic sets
#: Arabic through ``ConversationCopy.clarify_change``. No placeholders.
_CLARIFY_CHANGE_TEXT = "Sorry — which detail should I change?"

#: The read-back quick-reply buttons when a tenant configures none. English by default because the
#: read-back's own default wording is English too; a clinic sets both these and
#: ``confirm_read_back`` to Arabic together (``ConversationCopy.confirm_yes`` / ``confirm_no``).
_CONFIRM_YES_TEXT = "Yes"
_CONFIRM_NO_TEXT = "No"

#: A stored date spoken back the way the patient said it, in Egyptian Arabic. The task keeps
#: ``requested_date`` as an ISO string because that is what a booking acts on; reading "2026-09-02"
#: back is asking someone to confirm a string, and rendering it as "Wednesday 02 September" drops
#: English day and month names into an Arabic sentence — the pre-existing leak the demo has to
#: stop, not extend. Relative days cover the demo; a weekday name covers the rest without English.
_RELATIVE_DAYS_AR: dict[int, str] = {0: "النهاردة", 1: "بكرة", 2: "بعد بكرة"}
_WEEKDAYS_AR: dict[int, str] = {
    0: "الاتنين",
    1: "التلات",
    2: "الأربع",
    3: "الخميس",
    4: "الجمعة",
    5: "السبت",
    6: "الأحد",
}

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
    return OutboundAction(kind="handoff", text=current_copy().handoff or HANDOFF_TEXT), task


def _progress_signature(
    task: Task, vocabulary: Vocabulary
) -> tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Patient/business state whose change proves that the conversation advanced.

    Only slots declared by the active intent participate. Reserved task metadata therefore
    persists in ``Task.slots`` without becoming a fact, satisfying a required slot, resetting its
    own counter, or entering any patient/tool/rendering surface.
    """
    intent = next(item for item in vocabulary.intents if item.name == task.intent)
    declared = set(intent.required_slots) | set(intent.optional_slots)
    facts = tuple(
        sorted(
            (name, value)
            for name, value in task.slots.items()
            if name in declared and not is_internal_slot(name)
        )
    )
    confirmed = tuple(
        sorted(name for name in task.confirmed if name in declared and not is_internal_slot(name))
    )
    return task.intent, facts, confirmed


def _stored_non_progress_turns(task: Task, *, legacy_fallback: int) -> int:
    """Read the persisted counter, falling back for tasks created before this marker existed."""
    raw = task.slots.get(NON_PROGRESS_TURNS_SLOT)
    if raw is None:
        return max(0, legacy_fallback)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, legacy_fallback)


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
    original_task = task
    previous_progress = _progress_signature(task, vocab) if task is not None else None
    previous_non_progress = (
        _stored_non_progress_turns(task, legacy_fallback=turns_taken) if task is not None else 0
    )

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
    retrying_after_none = (
        task is not None
        and task.slots.get(AWAITING_ANOTHER_DATE_SLOT) == "1"
        and reads_as_yes(turn.text or "")
    )
    supplied: dict[str, str] = {}
    if not answering and task is not None and intent == _UNCLEAR:
        supplied = _read_as_answer(task, turn, vocab, today)
        # The same temporal provenance guard the worker applies to the classifier's slots (step 3),
        # now on the value this path reads straight out of the fragment. ``_read_as_answer`` fills
        # the awaited slot with ``normalise_slots``, whose ``parse_time`` will take the bare "6" out
        # of "جلسة رقم 6" and call it 18:00 — a fabricated time that would otherwise be held, read
        # back and booked. Re-checked against the same message it was read from, so a genuine
        # "الساعة ٦" or a bare-hour answer survives while an embedded number is dropped and the task
        # simply keeps waiting on the slot.
        supplied = strip_unsupported_temporal_slots(
            supplied, turn.text, today=today or turn.received_at.date()
        )

    if answering or supplied or retrying_after_none:
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

    if task is not None and task.intent != intent and _continues_task(task.intent, intent):
        # A compatible transition (see ``_COMPATIBLE_TRANSITIONS``): keep the task and everything it
        # has collected, and adopt the new intent. The branch and day an ``availability_check`` was
        # already holding stay on the ``booking_enquiry`` that continues it, so the booking is only
        # missing the time it offers rather than starting from an empty slate.
        task.intent = intent
    elif task is None or task.intent != intent:
        task = Task(intent=intent, vocabulary=vocab)

    supported_slots = strip_unsupported_clinic_slots(
        {**extracted_slots, **supplied}, turn.text, tenant_id=str(turn.tenant_id)
    )
    if retrying_after_none or "requested_date" in supported_slots:
        task.slots.pop(AWAITING_ANOTHER_DATE_SLOT, None)
    task.absorb(supported_slots)

    refused_confirmation = False
    if answering:
        if reads_as_yes(turn.text or ""):
            task.agree()
        else:
            # A refusal. Nothing is agreed and nothing is guessed about which detail was wrong;
            # the customer says so in their own words on the next turn, and `absorb` drops the
            # confirmation of anything they change. Two of these and `on_max_turns` fetches a
            # person, which is the right end for a read-back that keeps being rejected.
            refused_confirmation = True

    current_progress = _progress_signature(task, vocab)
    if original_task is None or task is not original_task:
        non_progress_turns = 0
    elif current_progress != previous_progress or retrying_after_none:
        non_progress_turns = 0
    else:
        non_progress_turns = previous_non_progress + 1
    task.slots[NON_PROGRESS_TURNS_SLOT] = str(non_progress_turns)

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

    # The budget applies to consecutive turns that add no business fact or confirmation. Useful
    # service refinement and newly supplied branch/date/time facts reset it; the reserved counter
    # survives rehydration but is excluded from the progress signature itself.
    if step in ("ask", "confirm") and non_progress_turns >= vocab.defaults.max_clarifying_turns:
        return await _hand_off(task, vocab.defaults.on_max_turns)

    if refused_confirmation:
        return (
            OutboundAction(
                kind="ask",
                text=current_copy().clarify_change or _CLARIFY_CHANGE_TEXT,
            ),
            task,
        )

    intent_def = next((i for i in vocab.intents if i.name == intent), None)
    tool_name = intent_def.terminal_tool if intent_def is not None else None

    if step == "ask":
        assert slot is not None
        if slot == _TIME_SLOT and tool_name == _BOOKING_TOOL:
            # Not an open question. The one detail still missing is *which* appointment, and the
            # only honest way to collect it is to offer what the diary actually holds.
            return await _offer_times(task, turn, conversation_id, vocab)
        if slot == _SERVICE_SLOT and intent in _SERVICE_ASK_INTENTS:
            # The one ask on the booking/availability flow, and the last English leak on it. Asked
            # in Arabic, carrying the branch and day the task already holds — not the generic slot
            # prompt. Gated on the intent so a `price_enquiry` or `preparation_aftercare_info` that
            # also lacks a service is not asked "which service would you like to book?".
            resolved_today = today or turn.received_at.date()
            fallback = _ask_for_service(task, resolved_today)
            # The renderer is told *which* slot to ask about through a deterministic ``{slot}``
            # descriptor, so it phrases the question the task chose — never one it picked.
            text = await render_reply(
                "ask_missing_slot", _slot_facts(_SERVICE_SLOT), fallback=fallback
            )
            return OutboundAction(kind="ask", text=text), task
        if (contextual := _ask_for_slot(slot)) is not None:
            # The branch/date/time asks between the service and the diary. Arabic for the clinic
            # booking slots (see `_CLINIC_SLOT_ASKS`); every other slot keeps the generic prompt.
            # The renderer may phrase the question warmly, but only about this exact slot (the
            # ``{slot}`` descriptor); failing, it is the Arabic fallback unchanged.
            text = await render_reply("ask_missing_slot", _slot_facts(slot), fallback=contextual)
            return OutboundAction(kind="ask", text=text), task
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
    return OutboundAction(kind="handoff", text=current_copy().unbuilt or _UNBUILT_TEXT), task


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


def _continues_task(previous_intent: str, new_intent: str) -> bool:
    """Whether a change of classified intent should continue the task rather than open a new one.

    True only for the one directed, superset-safe pair in ``_COMPATIBLE_TRANSITIONS``
    (``availability_check`` → ``booking_enquiry``). Every other relabel returns False and gets a
    fresh task, so this is a single compatible transition and not generic cross-intent merging.
    """
    return (previous_intent, new_intent) in _COMPATIBLE_TRANSITIONS


def _offered_concrete_slots(result: ToolResult) -> bool:
    """Whether an availability result actually put bookable times in front of the patient.

    True only for a successful offer carrying real times — the one case that is the start of a
    booking. "Nothing free" (``ok`` False, empty ``times``), an ambiguous service (a "which did you
    mean?" question) and an unresolved one all return False, so none of them is continued as a
    pending booking: they complete or re-ask exactly as before.
    """
    return result.ok and bool((result.data or {}).get("times"))


def _mark_concrete_offer(task: Task, turn: InboundTurn) -> None:
    """Record, on the task, that a concrete availability offer was just made and when.

    Called only where the availability result actually carries bookable slots (see
    ``_answer_from_catalogue`` and ``_offer_times``). The value is the *offer turn's* own
    ``received_at`` — the event timestamp, persisted with the task so the freshness window survives
    a process restart and never depends on when the row happened to be written. A "nothing free"
    answer, an ambiguous service, or any result without concrete times must never call this, because
    its presence is the proof that later distinguishes a real offer from an ordinary clarification.
    """
    task.slots[_OFFER_AT_SLOT] = turn.received_at.isoformat()


def _clear_concrete_offer(task: Task) -> None:
    """Drop any prior offer proof before a fresh booking-availability evaluation.

    An offer's freshness belongs to the exact service, branch and date it was made for. When the
    booking re-evaluates availability — the patient changed the day, or is being re-offered times —
    the earlier proof is superseded and must be cleared *before* the new result is known, so a
    re-evaluation that returns no concrete times (a day with nothing free) leaves the task with no
    proof at all rather than an orphaned one from the previous service/branch/date. Without this, an
    offer for A followed by a no-availability B would keep A's marker and later expire B, discarding
    the service/branch/date the patient gave for B.
    """
    task.slots.pop(_OFFER_AT_SLOT, None)


def _reset_after_none_available(task: Task) -> None:
    """Keep treatment/location but clear the failed day and state derived from that day."""
    _clear_concrete_offer(task)
    for slot in ("requested_date", _TIME_SLOT):
        task.slots.pop(slot, None)
        task.confirmed.discard(slot)
    task.slots[AWAITING_ANOTHER_DATE_SLOT] = "1"


def resumed_offer_is_stale(task: Task, now: datetime, vocab: Vocabulary) -> bool:
    """Whether a resumed booking's concrete availability offer has aged past the freshness window.

    The guard for the one state a **concrete** availability offer leaves behind: a
    ``booking_enquiry`` kept ``COLLECTING`` with service, branch and date held, only
    ``requested_time`` still missing, **and** the persisted proof that real slots were offered
    (``_OFFER_AT_SLOT``, written by ``_mark_concrete_offer`` and cleared by
    ``_clear_concrete_offer`` on every re-evaluation). A much later bare reply such as "الساعة ٧"
    must not be read against that old offer, because the diary it quoted has moved on: resuming it
    would let stale service/branch/date context reach a hold, read-back or booking.

    The offer proof is required, not inferred. Intent, status and a missing ``requested_time`` are
    not enough on their own — a booking whose day had nothing free sits in exactly that shape yet
    was never offered concrete times, and expiring it would wrongly discard the service, branch and
    date the patient did give. Only a task carrying ``_OFFER_AT_SLOT`` was genuinely offered slots,
    so only it is subject to the window; every other task (an ``availability_check``, a booking
    still missing service/branch, a no-availability clarification, a read-back awaiting yes/no, any
    finished job) returns ``False`` and is resumed exactly as before.

    The window is the clinic's existing ``quoting.max_age_seconds`` — the same contract the offer is
    quoted under, not a second TTL — and age is measured from the persisted offer timestamp to the
    inbound turn's own timestamp (``now``), so the decision is deterministic and never reads the
    machine clock.

    Once a task *is* a post-offer pending booking, a marker that is present but unusable — a
    non-string that JSON drift could leave behind, or a string that will not parse as a timestamp —
    is treated as **stale**, not fresh: its offer age cannot be trusted, and resuming it
    indefinitely (or raising on a wrong type) is the worse failure than dropping one booking whose
    metadata is already corrupt. A genuinely absent marker is the "no offer" case and stays fresh.
    """
    marker: object = task.slots.get(_OFFER_AT_SLOT)
    if marker is None:
        return False
    if task.intent != _BOOKING_INTENT or task.status != TaskStatus.COLLECTING:
        return False
    if task.next_step() != ("ask", _TIME_SLOT):
        return False
    if not isinstance(marker, str):
        return True
    try:
        offered_at = datetime.fromisoformat(marker)
    except ValueError:
        return True
    return _seconds_between(offered_at, now) > vocab.quoting.max_age_seconds


def _seconds_between(earlier: datetime, later: datetime) -> float:
    """Elapsed seconds, tolerating the naive datetimes SQLite hands back (read as UTC)."""
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=UTC)
    if later.tzinfo is None:
        later = later.replace(tzinfo=UTC)
    return (later - earlier).total_seconds()


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


def _ask_for_service(task: Task, today: date) -> str:
    """The missing-service question, rendered from the branch and day the task already holds.

    Both are optional: a booking can reach here with only one of them, or neither, and the sentence
    has to read in every case. So the branch and day are turned into *fragments* — "في فرع المعادي",
    "بكرة", or empty — and the template carries the connective words with them, the same way
    ``_read_back`` composes its own sentence. Nothing is resolved against the catalogue here (that
    is the tool's job on the next turn): the branch is the patient's own word, kept as they wrote
    it, so the question stays in their language rather than reading a branch name back in English.
    """
    branch = task.slots.get("branch")
    branch_part = f" في فرع {branch}" if branch else ""
    spoken = _spoken_day(task.slots.get("requested_date"), today)
    date_part = f" {spoken}" if spoken else ""
    template = current_copy().ask_service or _ASK_SERVICE_TEXT
    return fill_template(template, branch=branch_part, date=date_part) or _ASK_SERVICE_TEXT.format(
        branch=branch_part, date=date_part
    )


#: Which ``ConversationCopy`` field overrides each clinic slot ask. Kept beside
#: ``_CLINIC_SLOT_ASKS`` so adding a slot is one entry in each.
_CLINIC_SLOT_COPY: dict[str, Callable[[ConversationCopy], str | None]] = {
    "branch": lambda copy: copy.ask_branch,
    "requested_date": lambda copy: copy.ask_date,
    _TIME_SLOT: lambda copy: copy.ask_time,
}


def _ask_for_slot(slot: str) -> str | None:
    """The Arabic question for a missing clinic booking slot, or ``None`` for any other slot.

    ``None`` means "not one of the clinic booking slots" and the caller falls back to the generic
    English prompt — the branch/date/time slots are clinic-only, so returning a sentence here for
    ``check_in`` would put Arabic in a holiday-home booking. The tenant's own wording wins through
    ``current_copy()``, exactly as ``_ask_for_service`` does, and a template that will not render
    degrades to the Arabic default rather than raising mid-booking.
    """
    default = _CLINIC_SLOT_ASKS.get(slot)
    if default is None:
        return None
    override = _CLINIC_SLOT_COPY[slot](current_copy())
    template = override or default
    return fill_template(template) or default


def _spoken_day(value: str | None, today: date) -> str | None:
    """An ISO date as a patient would say the day in Egyptian Arabic, or ``None``.

    Relative for the days a booking actually lands on ("بكرة"), a weekday name otherwise, and
    nothing at all for a value that is not a date — an empty fragment the caller drops, rather than
    a broken sentence or an English date inside an Arabic one.
    """
    if not value:
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    offset = (day - today).days
    if offset in _RELATIVE_DAYS_AR:
        return _RELATIVE_DAYS_AR[offset]
    return f"يوم {_WEEKDAYS_AR[day.weekday()]}"


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


# ── Renderer facts (demo step 5): the proven values each eligible act may place ───────────────
#
# Every value here is deterministic — a task slot the patient established, a spoken day derived from
# a stored ISO date, the diary's own times, the scheduling system's own reference. The renderer
# phrases *around* these; it never chooses them. Only the values that are actually known are
# included, so ``RenderSpec`` requires exactly what an act needs and no template can reference a
# fact the turn has not proven. The Arabic spoken day is used rather than ``_readable``'s English
# formatting so a *generated* reply stays fully Arabic — the deterministic fallback (which does use
# the English date) still stands if generation fails.


def _known_booking_facts(task: Task, today: date) -> dict[str, str]:
    """The service, branch and spoken day this task already holds, whichever are present."""
    facts: dict[str, str] = {}
    if service := task.slots.get(_SERVICE_SLOT):
        facts["service"] = service
    if branch := task.slots.get("branch"):
        facts["branch"] = branch
    if spoken := _spoken_day(task.slots.get("requested_date"), today):
        facts["date"] = spoken
    return facts


def _offer_facts(task: Task, result: ToolResult, today: date) -> dict[str, str]:
    """The offer's facts: the diary's own times, plus the service, branch and day held."""
    facts = _known_booking_facts(task, today)
    times = (result.data or {}).get("times") or []
    if times:
        facts["times"] = " / ".join(str(t) for t in times)
    return facts


def _read_back_facts(task: Task, today: date) -> dict[str, str]:
    """The booking details being read back: service, branch, spoken day and the chosen time."""
    facts = _known_booking_facts(task, today)
    if chosen := task.slots.get(_TIME_SLOT):
        facts["time"] = chosen
    return facts


def _booking_facts(task: Task, reference: str, today: date) -> dict[str, str]:
    """The confirmed booking's facts: the durable reference, plus the details it was made for."""
    facts = _read_back_facts(task, today)
    facts["booking_reference"] = reference
    return facts


#: The deterministic Egyptian-Arabic descriptor of each clinic slot a missing-slot question asks
#: about. Passed to the renderer as the required ``{slot}`` fact so the model asks about the slot
#: the task chose — a proven value it substitutes, never a question it picks. Substituted after
#: validation, so these words are not part of the renderer's safe vocabulary.
_SLOT_DESCRIPTORS: dict[str, str] = {
    _SERVICE_SLOT: "الخدمة اللي تحبي تحجزيها",
    "branch": "الفرع اللي يناسبك",
    "requested_date": "اليوم اللي يناسبك",
    _TIME_SLOT: "الساعة اللي تحبيها",
}


def _slot_facts(slot: str) -> dict[str, str]:
    """The ``{slot}`` descriptor fact for a missing-slot ask, or ``{}`` for a non-clinic slot.

    Empty for any slot without a descriptor, which makes the ``ask_missing_slot`` spec fail its
    required fact and fall back deterministically without a model call — the renderer never phrases
    a question about a slot it cannot name.
    """
    descriptor = _SLOT_DESCRIPTORS.get(slot)
    return {"slot": descriptor} if descriptor else {}


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
    # Re-evaluating availability for this booking: any earlier offer proof is superseded. Clear it
    # first, so a result with no concrete times (a changed day with nothing free) leaves no marker
    # rather than an orphaned one from the previous service/branch/date.
    _clear_concrete_offer(task)
    result = await _availability(task, turn, conversation_id)
    if result is None:
        return await _hand_off(task, "handoff_to_human")
    if (block := _screen_category(result, vocab)) is not None:
        return await _blocked(task, block)
    if result.human_summary:
        today = turn.received_at.date()
        # Only a result carrying real slots is a concrete offer. "Nothing free on Thursday" also
        # has a human_summary and leaves the task in the same ``COLLECTING`` / waiting-for-time
        # shape, but it offered nothing — so it must *not* be re-marked, or its later resume would
        # be expired and the patient's service/branch/date discarded.
        if _offered_concrete_slots(result):
            _mark_concrete_offer(task, turn)
            text = await render_reply(
                "offer_times", _offer_facts(task, result, today), fallback=result.human_summary
            )
            return OutboundAction(kind="ask", text=text), task
        if result.error == "none_available":
            # A real answer to a real question, eligible for warm phrasing. Gated strictly on the
            # "nothing free" error so an ambiguous-service "which did you mean?" (also a
            # ``human_summary`` with no concrete times) is never routed through the renderer.
            text = await render_reply(
                "nothing_free", _known_booking_facts(task, today), fallback=result.human_summary
            )
            _reset_after_none_available(task)
            return OutboundAction(kind="ask", text=text), task
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
    copy = current_copy()
    template = copy.confirm_read_back or _READ_BACK_TEXT
    fallback = fill_template(template, details=details, values=values) or _READ_BACK_TEXT.format(
        details=details
    )
    # The renderer may phrase the read-back naturally — but it may never claim the booking is done
    # (the validator rejects a confirmation claim on any non-``booking_confirmed`` act), so this
    # stays a question the patient answers. Only the clinic booking shape (service/branch/date/time)
    # supplies the required facts; any other read-back falls back deterministically without a call.
    text = await render_reply(
        "read_back", _read_back_facts(task, turn.received_at.date()), fallback=fallback
    )
    return (
        OutboundAction(
            kind="confirm",
            text=text,
            # The buttons beside an Arabic read-back were the last English on the turn. A tenant
            # sets them in its own language (`confirm_yes`/`confirm_no`); `confirmation.py` reads
            # "أيوه"/"لأ" as agreement/refusal, so an Arabic button still books or corrects.
            quick_replies=[
                copy.confirm_yes or _CONFIRM_YES_TEXT,
                copy.confirm_no or _CONFIRM_NO_TEXT,
            ],
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
    question ("تحبي أنهي واحدة فيهم: Basic Facial / Facial؟") and it is asked, because picking one
    would quote a real price for a treatment nobody asked about. A tool that came back with
    nothing to say at all falls through to ``on_tool_failure``, which fetches a person — the
    vocabulary's own answer, and the only safe one when a quote cannot be sourced.
    """
    tool = REGISTRY.get(tool_name)
    if tool is None:
        return await _unbuilt(task)

    if tool_name == _AVAILABILITY_TOOL:
        # Re-evaluating availability: supersede any earlier offer proof before this result is known,
        # so only a concrete offer below re-marks the task (see ``_clear_concrete_offer``).
        _clear_concrete_offer(task)

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
        if tool_name == _AVAILABILITY_TOOL and _offered_concrete_slots(result):
            # A successful availability offer with real times is the *start* of a booking, not the
            # end of a question. Completing the task now would drop it from the active set — both
            # the store's ``get_active_task`` and the eval mirror key continuity off status — so
            # the patient's next message (a bare time like "الساعة ٧", classified ``unclear``
            # because out of context it is two words carrying no service, branch or date) would
            # begin from an empty slate and hand off. Instead the *same* task is continued as a
            # ``booking_enquiry``: it already holds the service, branch and date the booking needs
            # and is now only missing the time it just offered, which the ``unclear``-fragment rule
            # reads into ``requested_time`` and carries into the read-back. This is the
            # ``availability_check`` → ``booking_enquiry`` transition of ``_COMPATIBLE_TRANSITIONS``
            # reached by a real offer rather than by the classifier, and superset-safe for the same
            # reason. The offer text is unchanged — the patient still reads it as a ``say``.
            #
            # The clinical gate first, because this is the transition that makes a booking pending:
            # an ``availability_check`` is not ``_is_booking`` (its terminal tool is
            # ``check_availability``, not ``confirm_booking``), so the turn-text screen in
            # ``handle`` never ran for this turn. Both halves of the gate are applied here before
            # the task is converted — the patient's words this turn, so a disclosure such as
            # pregnancy is caught, and the treatment the catalogue just resolved, so a screened
            # category such as an injectable is stopped — using the same ``screen`` call ``handle``
            # uses and the same ``_screen_category`` the offer path uses. Either block takes the
            # existing clinical hand-off and the task is never converted, so hold, read-back and
            # booking are never reached. The disclosure check runs first, mirroring ``screen``'s
            # own precedence.
            if (block := screen(turn.text, vocabulary=vocab)) is not None:
                return await _blocked(task, block)
            if (block := _screen_category(result, vocab)) is not None:
                return await _blocked(task, block)
            # Concrete slots really were offered this turn: record the proof and the offer's own
            # timestamp on the task (see ``_mark_concrete_offer``), so a later resume can tell this
            # pending booking from an ordinary clarification and measure the offer's age.
            _mark_concrete_offer(task, turn)
            task.intent = _BOOKING_INTENT
            task.status = TaskStatus.COLLECTING
            text = await render_reply(
                "offer_times",
                _offer_facts(task, result, turn.received_at.date()),
                fallback=result.human_summary,
            )
            return OutboundAction(kind="say", text=text), task
        task.status = TaskStatus.COMPLETED if result.ok else TaskStatus.COLLECTING
        if tool_name == _AVAILABILITY_TOOL and not result.ok and result.error == "none_available":
            # "Nothing free" on a pure availability check — eligible for warm phrasing. Never the
            # ambiguous-service question (a different ``error``) and never a price quote (a
            # different ``tool_name``): those keep their exact deterministic wording.
            text = await render_reply(
                "nothing_free",
                _known_booking_facts(task, turn.received_at.date()),
                fallback=result.human_summary,
            )
            _reset_after_none_available(task)
            return OutboundAction(kind="ask", text=text), task
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
    fallback = fill_template(template, booking_reference=str(reference)) or _BOOKED_TEXT.format(
        booking_reference=reference
    )
    # The one act where a confirmation claim is allowed — and it is only reached here, after a real
    # durable reference came back from the scheduling system. ``booking_reference`` is a required
    # placeholder, so a generation that drops it is rejected and the deterministic sentence (which
    # states the same reference) stands: the model can only phrase the confirmation, never fabricate
    # or omit the reference that makes it true.
    text = await render_reply(
        "booking_confirmed",
        _booking_facts(task, str(reference), turn.received_at.date()),
        fallback=fallback,
    )
    return OutboundAction(kind="say", text=text), task


async def _unbuilt(task: Task) -> tuple[OutboundAction, Task]:
    """Record the message and fetch a person. Never a success reply — see ``_UNBUILT_TEXT``."""
    take_message = REGISTRY.get("take_message")
    if take_message is not None:
        await take_message.run()
    task.status = TaskStatus.HANDED_OFF
    return OutboundAction(kind="handoff", text=current_copy().unbuilt or _UNBUILT_TEXT), task


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
