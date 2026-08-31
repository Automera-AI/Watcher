"""Tests for the receptionist function (Item 2.2)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from packages.intents.schema import shipped_vocabularies

from apps.api.conversations.receptionist import _UNBUILT_TEXT, HANDOFF_TEXT, handle
from apps.api.conversations.task import Task, TaskStatus
from apps.api.conversations.tools import (
    REGISTRY,
    AnswerFromKnowledge,
    CloseConversation,
    ConversationCopy,
    Greet,
    configure_conversation_copy,
    current_copy,
)
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


def test_all_slots_ready_reaches_execution() -> None:
    """A task with nothing left to ask stops asking and runs its terminal tool.

    ``availability_check`` names ``check_availability``, which is not built yet, so the honest
    outcome is a hand-off carrying ``_UNBUILT_TEXT``. It asserted ``say`` and ``COMPLETED`` while
    the receptionist answered every unbuilt tool with "All set! I've noted everything down." —
    telling someone who asked to book that they had a booking, when nothing had been written
    anywhere. What the test is really pinning is that the task got *past* the ask/confirm steps,
    and that is what it now checks.
    """
    task = Task(
        intent="availability_check",
        slots={"check_in": "Jan 15", "check_out": "Jan 20"},
        confirmed={"check_in", "check_out"},
    )
    action, task = asyncio.run(handle(_turn(), "availability_check", 0.95, {}, task))
    assert action.kind not in ("ask", "confirm")
    assert action.text == _UNBUILT_TEXT
    assert task.status == TaskStatus.HANDED_OFF


def test_an_unbuilt_tool_never_claims_the_job_is_done() -> None:
    """The regression that matters most on a booking journey.

    Every terminal tool without an implementation used to answer "All set! I've noted everything
    down." A patient who has just asked for an appointment reads that as confirmation, and then
    arrives at a clinic that has never heard of them. An unbuilt capability is a hand-off, and no
    unbuilt path may produce success wording.
    """
    for intent in ("availability_check", "price_enquiry", "booking_enquiry"):
        task = Task(intent=intent)
        task.slots = dict.fromkeys(task.required, "given")
        task.confirmed = set(task.required)

        action, updated = asyncio.run(handle(_turn(), intent, 0.95, {}, task))

        assert updated.status is TaskStatus.HANDED_OFF, intent
        assert "All set" not in (action.text or ""), intent
        assert action.kind == "handoff", intent


_CLINICS = shipped_vocabularies()["clinics"]


def test_a_greeting_is_answered_rather_than_escalated() -> None:
    """The single worst behaviour in the receptionist before the clinic vertical existed.

    "Hi" had two routes and both ended in "Let me connect you with someone who can help": the
    classifier had no ``greeting`` member so it returned ``unclear``, whose ceiling is
    ``hand_off``; or it returned ``general_info``, whose ``answer_from_knowledge`` searched the
    facts table for "hi", found nothing, and fell through ``on_no_knowledge`` to the same place.
    A greeting is not a failure to understand.
    """
    action, task = asyncio.run(
        handle(_turn("السلام عليكم"), "greeting", 0.95, {}, None, vocabulary=_CLINICS)
    )
    assert action.kind == "say"
    assert action.text != HANDOFF_TEXT
    assert task.status is TaskStatus.COMPLETED


def test_a_greeting_uses_the_name_the_channel_supplied() -> None:
    """The profile name is a slot the customer never typed, so it arrives via extracted_slots."""
    action, _task = asyncio.run(
        handle(_turn("hi"), "greeting", 0.95, {"customer_name": "Rana"}, None, vocabulary=_CLINICS)
    )
    assert "Rana" in (action.text or "")


def test_a_greeting_without_a_name_still_greets() -> None:
    """A withheld profile name is normal on WhatsApp and is not a reason to fetch a person."""
    action, task = asyncio.run(handle(_turn("hi"), "greeting", 0.95, {}, None, vocabulary=_CLINICS))
    assert action.kind == "say"
    assert task.status is TaskStatus.COMPLETED


def test_thanks_closes_the_conversation_instead_of_escalating() -> None:
    """A completed booking that ends in a hand-off looks like a failed one.

    The demo journey ends with the customer saying thanks; escalating that puts a person in front
    of someone who was only saying goodbye.
    """
    action, task = asyncio.run(
        handle(_turn("شكرا"), "thanks_closing", 0.95, {}, None, vocabulary=_CLINICS)
    )
    assert action.kind == "say"
    assert action.text != HANDOFF_TEXT
    assert task.status is TaskStatus.COMPLETED


@pytest.mark.parametrize("intent", ["clinical_question", "clinical_urgent"])
def test_clinical_intents_always_fetch_a_person(intent: str) -> None:
    """The boundary the clinic vertical exists to hold, at full confidence.

    Suitability and post-treatment reactions are medical judgement. ``SAFETY["clinics"]`` pins
    both to ``hand_off`` so no edit to the vocabulary can turn either into something answered
    alone; this checks the receptionist honours it rather than merely that the YAML declares it.
    """
    action, task = asyncio.run(
        handle(_turn("أنا حامل ينفع أعمل ليزر؟"), intent, 0.99, {}, None, vocabulary=_CLINICS)
    )
    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF


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

    def search(self, tenant_id: str, property_id: str | None = None) -> list[Fact]:
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

    # Past the budget guard: it executed rather than being cut off. ``check_availability`` is
    # unbuilt, so execution ends in the unbuilt hand-off and not in the max-turns one — the two
    # are told apart by their wording, which is the only thing distinguishing them from outside.
    assert action.text == _UNBUILT_TEXT
    assert action.text != HANDOFF_TEXT
    assert updated.status == TaskStatus.HANDED_OFF


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


# ── The contextual missing-service ask (DermaClub diagnosis, first fix) ───────────────────────


def test_missing_service_is_asked_in_contextual_arabic() -> None:
    """The screenshot's first divergence: a branch and a day given, the service missing.

    The generic English "Could you please provide the service?" is replaced by an Egyptian Arabic
    question that carries the branch and the day the task already holds, and — the point of the
    seam — the branch and date survive the turn, so the next message resolves against them.
    """
    action, task = asyncio.run(
        handle(
            _turn("عايزة احجز بكرة في المعادي ايه المتاح؟"),
            "availability_check",
            0.94,
            {"branch": "المعادي", "requested_date": "2026-09-02"},
            None,
            vocabulary=_CLINICS,
            today=date(2026, 9, 1),
        )
    )
    assert action.kind == "ask"
    assert action.text == "أكيد، تحبي تحجزي أنهي خدمة في فرع المعادي بكرة؟"
    assert "Could you please provide" not in (action.text or "")
    assert task.slots["branch"] == "المعادي"
    assert task.slots["requested_date"] == "2026-09-02"


def test_missing_service_ask_degrades_without_context() -> None:
    """Branch and day are optional context. With neither, the one template still reads."""
    action, _ = asyncio.run(
        handle(
            _turn("عايزة احجز"),
            "availability_check",
            0.94,
            {},
            None,
            vocabulary=_CLINICS,
            today=date(2026, 9, 1),
        )
    )
    assert action.kind == "ask"
    assert action.text == "أكيد، تحبي تحجزي أنهي خدمة؟"


def test_a_tenant_may_override_the_missing_service_wording() -> None:
    """The ask goes through ``current_copy()``: a tenant's own phrasing wins, with the same
    branch and day fragments filled in — the Arabic default is only what a tenant that set nothing
    still says."""
    previous = current_copy()
    configure_conversation_copy(ConversationCopy(ask_service="تحبي تحجزي ايه{branch}{date}؟"))
    try:
        action, _ = asyncio.run(
            handle(
                _turn("احجز"),
                "availability_check",
                0.94,
                {"branch": "المعادي", "requested_date": "2026-09-02"},
                None,
                vocabulary=_CLINICS,
                today=date(2026, 9, 1),
            )
        )
    finally:
        configure_conversation_copy(previous)
    assert action.text == "تحبي تحجزي ايه في فرع المعادي بكرة؟"


@pytest.mark.parametrize(
    ("intent", "extra_slots"),
    [
        ("price_enquiry", {}),
        ("preparation_aftercare_info", {"instruction_phase": "preparation"}),
    ],
)
def test_a_non_booking_service_intent_keeps_the_generic_ask(
    intent: str, extra_slots: dict[str, str]
) -> None:
    """The contextual "…which service would you like to book?" is for the booking/availability flow
    only. ``price_enquiry`` (a quote) and ``preparation_aftercare_info`` (a how-to) also require a
    ``service``, but neither is booking anything — so a missing service on them must fall to the
    generic slot prompt, never the booking-specific Arabic ask. Gating on the slot alone would give
    a patient asking a price "which service would you like to book?", which is the wrong question.
    """
    action, task = asyncio.run(
        handle(
            _turn("عايزة اعرف"),
            intent,
            0.95,
            extra_slots,
            None,
            vocabulary=_CLINICS,
            today=date(2026, 9, 1),
        )
    )
    assert action.kind == "ask"
    # The booking-specific ask is not taken: the generic slot prompt is, naming the service slot.
    assert action.text == "Could you please provide the service?"
    assert "تحجزي" not in (action.text or "")
    assert task.status == TaskStatus.COLLECTING


# ── The missing branch / date / time asks (step 4) ───────────────────────────────────────────


def test_missing_branch_is_asked_in_arabic() -> None:
    """The turn after the service: ``برايم ليز`` given, the branch outstanding.

    The step-by-step booking journey asks for the branch and then the day between the service and
    the diary, and both used to be answered by the generic English ``Could you please provide the
    …?``. The branch ask is now Egyptian Arabic with no configuration, keyed to the clinic-only
    ``branch`` slot, and the service it already holds survives the turn.
    """
    action, task = asyncio.run(
        handle(
            _turn("برايم ليز"),
            "booking_enquiry",
            0.95,
            {"service": "برايم ليز"},
            None,
            vocabulary=_CLINICS,
            today=date(2026, 9, 1),
        )
    )
    assert action.kind == "ask"
    assert action.text == "تمام، تحبي تحجزي في أنهي فرع؟"
    assert "Could you please provide" not in (action.text or "")
    assert task.slots["service"] == "برايم ليز"


def test_missing_date_is_asked_in_arabic() -> None:
    """Service and branch given, the day outstanding — asked in Arabic, not the generic prompt."""
    action, _ = asyncio.run(
        handle(
            _turn("المعادي"),
            "booking_enquiry",
            0.95,
            {"service": "برايم ليز", "branch": "المعادي"},
            None,
            vocabulary=_CLINICS,
            today=date(2026, 9, 1),
        )
    )
    assert action.kind == "ask"
    assert action.text == "تمام، تحبي الحجز يكون يوم ايه؟"
    assert "Could you please provide" not in (action.text or "")


def test_a_tenant_may_override_the_branch_and_date_asks() -> None:
    """The branch/date asks go through ``current_copy()``: a tenant's own phrasing wins."""
    previous = current_copy()
    configure_conversation_copy(
        ConversationCopy(ask_branch="في أنهي فرع؟", ask_date="امتى تحبي الميعاد؟")
    )
    try:
        branch, _ = asyncio.run(
            handle(
                _turn("برايم ليز"),
                "booking_enquiry",
                0.95,
                {"service": "برايم ليز"},
                None,
                vocabulary=_CLINICS,
                today=date(2026, 9, 1),
            )
        )
        date_ask, _ = asyncio.run(
            handle(
                _turn("المعادي"),
                "booking_enquiry",
                0.95,
                {"service": "برايم ليز", "branch": "المعادي"},
                None,
                vocabulary=_CLINICS,
                today=date(2026, 9, 1),
            )
        )
    finally:
        configure_conversation_copy(previous)
    assert branch.text == "في أنهي فرع؟"
    assert date_ask.text == "امتى تحبي الميعاد؟"


def test_a_non_clinic_booking_slot_keeps_the_generic_english_ask() -> None:
    """The Arabic asks are keyed to the clinic booking slots, not to the booking intent.

    A holiday-home ``booking_enquiry`` asks for ``check_in`` / ``unit_type`` — slots the clinic
    vocabulary never has — so it must keep the generic English prompt rather than pick up an Arabic
    sentence meant for a clinic. This is what keeps a shared code path from leaking Arabic into the
    other vertical (``_ask_for_slot`` returns ``None`` for any non-clinic slot).
    """
    holiday = shipped_vocabularies()["holiday_homes"]
    action, _ = asyncio.run(
        handle(
            _turn("I'd like to book the 2 bed"),
            "booking_enquiry",
            0.95,
            {"unit_type": "2 bed", "check_out": "Jan 20", "guests": "4"},
            None,
            vocabulary=holiday,
        )
    )
    assert action.kind == "ask"
    assert action.text == "Could you please provide the check in?"


# ── The safety exits stay deterministic, English by default, Arabic when set (step 4) ────────


def test_handoff_and_unbuilt_default_to_english_and_take_the_tenants_arabic() -> None:
    """Two safety surfaces the renderer never owns: a clinic makes them Arabic through the seam.

    Their in-code defaults are neutral English because they are shared with every vertical — a
    global Arabic default would put Arabic in a holiday-home hand-off. So the default is English and
    the clinic's configured wording overrides it, the same seam every other line goes through.
    """
    task = None
    for _attempt in range(6):
        default_handoff, task = asyncio.run(
            handle(_turn("؟"), "availability_check", 0.95, {}, task, vocabulary=_CLINICS)
        )
    assert default_handoff.kind == "handoff"
    assert default_handoff.text == HANDOFF_TEXT  # neutral English default

    previous = current_copy()
    configure_conversation_copy(
        ConversationCopy(handoff="هحوّلك لزميلي حالاً.", unbuilt="هراجع وأرجعلك.")
    )
    try:
        task = None
        for _attempt in range(6):
            arabic_handoff, task = asyncio.run(
                handle(_turn("؟"), "availability_check", 0.95, {}, task, vocabulary=_CLINICS)
            )
        task = Task(
            intent="availability_check",
            slots={"service": "س", "branch": "ب", "requested_date": "2026-09-02"},
            confirmed={"service", "branch", "requested_date"},
            vocabulary=_CLINICS,
        )
        unbuilt, _ = asyncio.run(
            handle(_turn("المواعيد"), "availability_check", 0.95, {}, task, vocabulary=_CLINICS)
        )
    finally:
        configure_conversation_copy(previous)
    assert arabic_handoff.text == "هحوّلك لزميلي حالاً."
    assert unbuilt.text == "هراجع وأرجعلك."
    assert unbuilt.text != _UNBUILT_TEXT


# ── Continuing a task across the availability → booking transition ────────────────────────────


def test_availability_to_booking_continues_the_task_and_keeps_branch_and_date() -> None:
    """The compatible transition: an availability check that names a treatment becomes the booking.

    A patient asks what is free at Maadi tomorrow (``availability_check``, branch + day, no
    service) and the next turn names the treatment. The classifier relabels that turn
    ``booking_enquiry``. The receptionist continues the *same* task rather than resetting it, so
    the branch and day the availability check already collected stay on the booking — which is why
    the booking is only missing the time it offers, not asking for a branch and day the patient
    gave a turn ago. See ``_COMPATIBLE_TRANSITIONS``.

    The clinic tools are not wired in this unit test, so the offer step ends in a hand-off; the
    proof that the offer is actually *reached* against a real diary (the ``19:00``) is in
    ``packages/eval/tests/test_screenshot_regression.py``. What this pins is the state carried
    across the transition: the intent adopted and the slots kept.
    """
    task = Task(
        intent="availability_check",
        slots={"branch": "المعادي", "requested_date": "2026-09-02"},
        vocabulary=_CLINICS,
    )
    _action, task = asyncio.run(
        handle(
            _turn("فاشيال"),
            "booking_enquiry",
            0.95,
            {"service": "فاشيال"},
            task,
            vocabulary=_CLINICS,
            conversation_id="conv-continues",
            today=date(2026, 9, 1),
        )
    )
    assert task.intent == "booking_enquiry"
    assert task.slots["branch"] == "المعادي"
    assert task.slots["requested_date"] == "2026-09-02"
    assert task.slots["service"] == "فاشيال"


@pytest.mark.parametrize(
    "next_intent",
    ["price_enquiry", "service_question", "greeting", "thanks_closing"],
)
def test_an_unrelated_intent_change_does_not_inherit_booking_state(next_intent: str) -> None:
    """The other side of the guard: only the one compatible pair continues a task.

    A booking task holding a branch, a day and a service must not have any of it survive into an
    unrelated request. If the patient stops booking and asks a price, greets, or thanks and leaves,
    the new task starts clean — the branch and day were part of the booking, not the new subject.
    A reset that leaked them would quote or answer against a context the patient never gave for
    that question, which is exactly the generic cross-intent merging this change does not build.
    """
    task = Task(
        intent="booking_enquiry",
        slots={"branch": "المعادي", "requested_date": "2026-09-02", "service": "فاشيال"},
        vocabulary=_CLINICS,
    )
    _action, task = asyncio.run(
        handle(
            _turn("سؤال تاني"),
            next_intent,
            0.95,
            {},
            task,
            vocabulary=_CLINICS,
            today=date(2026, 9, 1),
        )
    )
    assert task.intent == next_intent
    assert "branch" not in task.slots
    assert "requested_date" not in task.slots
    assert "service" not in task.slots


def test_the_reverse_transition_does_not_continue_the_task() -> None:
    """The compatible pair is directed: ``availability_check`` → ``booking_enquiry`` only.

    A booking that is relabelled ``availability_check`` is not the same journey continuing — the
    time it had started to pin is not a slot an availability check collects — so it resets rather
    than dragging a half-finished booking's state into a browse.
    """
    task = Task(
        intent="booking_enquiry",
        slots={"branch": "المعادي", "requested_date": "2026-09-02", "service": "فاشيال"},
        vocabulary=_CLINICS,
    )
    _action, task = asyncio.run(
        handle(
            _turn("ايه المتاح تاني؟"),
            "availability_check",
            0.94,
            {},
            task,
            vocabulary=_CLINICS,
            today=date(2026, 9, 1),
        )
    )
    assert task.intent == "availability_check"
    assert "branch" not in task.slots
    assert "requested_date" not in task.slots
    assert "service" not in task.slots


# ── Tenant conversation copy ─────────────────────────────────────────────────────────────────
#
# The wording here is deliberately fake. Real client copy names the client, and this repo's own
# `test_no_client_name.py` forbids that — the live lines are set as environment configuration.

_COPY = ConversationCopy(
    opening="Welcome to the clinic. How can I help?",
    opening_named="Welcome to the clinic, {customer_name}. How can I help?",
    closing="Thanks for getting in touch. Have a lovely day.",
    closing_booking_confirmed="Your booking is confirmed. Reference: {booking_reference}.",
)


@pytest.fixture
def tenant_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(REGISTRY, "greet", Greet(_COPY))
    monkeypatch.setitem(REGISTRY, "close_conversation", CloseConversation(_COPY))


def test_the_tenant_opening_is_used_verbatim(tenant_copy: None) -> None:
    """A tenant's opening is a whole message, not a fragment something else prefixes.

    The plain and named openings are separate sentences precisely so this holds in a language
    where a name does not sit at the front — gluing "Hello Rana!" onto an Egyptian Arabic opening
    would produce a bilingual mess no client would sign off.
    """
    action, _ = asyncio.run(handle(_turn("hi"), "greeting", 0.95, {}, None, vocabulary=_CLINICS))
    assert action.text == _COPY.opening


def test_the_named_opening_carries_the_profile_name(tenant_copy: None) -> None:
    action, _ = asyncio.run(
        handle(_turn("hi"), "greeting", 0.95, {"customer_name": "Rana"}, None, vocabulary=_CLINICS)
    )
    assert action.text == "Welcome to the clinic, Rana. How can I help?"


def test_closing_without_a_booking_never_claims_one(tenant_copy: None) -> None:
    """The regression this whole seam exists to prevent, one language further along.

    "Your booking is confirmed" is the same lie as "All set! I've noted everything down." — said
    with more authority, and in the customer's own language. A closing may only say it when the
    scheduling system actually returned a reference.
    """
    action, _ = asyncio.run(
        handle(_turn("thanks"), "thanks_closing", 0.95, {}, None, vocabulary=_CLINICS)
    )
    assert action.text == _COPY.closing
    assert "confirmed" not in (action.text or "")


def test_closing_names_the_reference_when_there_is_one(tenant_copy: None) -> None:
    task = Task(intent="thanks_closing", vocabulary=_CLINICS)
    task.slots["booking_reference"] = "DC-0042"
    action, _ = asyncio.run(
        handle(_turn("thanks"), "thanks_closing", 0.95, {}, task, vocabulary=_CLINICS)
    )
    assert action.text == "Your booking is confirmed. Reference: DC-0042."


def test_a_typo_in_tenant_copy_degrades_instead_of_crashing() -> None:
    """Copy is written by someone who is not looking at this code.

    ``str.format`` on a mistyped placeholder raises ``KeyError`` mid-conversation, which would
    lose the customer's reply entirely. A copy typo should cost a plainer message, nothing more.
    """
    broken = ConversationCopy(
        opening_named="Hello {custmer_name}!",
        closing_booking_confirmed="Confirmed: {booking_ref}.",
        closing="Thanks!",
    )
    named = asyncio.run(Greet(broken).run(customer_name="Rana"))
    closed = asyncio.run(CloseConversation(broken).run(booking_reference="DC-1"))

    assert named.ok and "{" not in (named.human_summary or "")
    assert closed.human_summary == "Thanks!", "a broken booking template must not be sent"
