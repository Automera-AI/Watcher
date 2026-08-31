"""The fact-locked renderer through the receptionist (pre-demo Step 5, plan §8–§12).

Where ``test_renderer.py`` pins the validator in isolation, this drives the *receptionist* in
generative mode: it proves the renderer runs on exactly the five eligible acts and never on a
safety surface, that the whole concrete booking journey sounds natural while every transactional
value stays deterministic, and — the persistence invariant — that the accepted generated sentence
is the exact text persisted, delivered, and returned on ``ProcessOutcome.outbound_action``.

The clinic diary and DermaClub Arabic copy are the same fixtures the deterministic journey uses
(``test_booking_journey``); only the renderer changes. A canned provider stands in for the model
so the tests are deterministic and offline — its job is to return a *valid* phrasing for each act
so the validator's accept path, not just its reject paths, is exercised end to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from apps.api.classifier.service import Classifier
from apps.api.conversations import renderer as renderer_module
from apps.api.conversations import tools
from apps.api.conversations.receptionist import handle
from apps.api.conversations.receptionist import handle as receptionist_handle
from apps.api.conversations.renderer import (
    _EXEMPLARS,
    GenerativeRenderer,
    RenderSpec,
    TemplateRenderer,
    configure_renderer,
)
from apps.api.conversations.task import Task, TaskStatus
from apps.api.conversations.tools import (
    REGISTRY,
    CheckAvailability,
    CloseConversation,
    ConfirmBooking,
    Greet,
    HoldSlot,
    QuotePrice,
)
from apps.api.core.clinic import ClinicDirectory
from apps.api.core.policy import DEFAULT_POLICY
from apps.api.orchestration.ports import ConversationState
from apps.api.orchestration.worker import Orchestrator, RoutingAction
from apps.api.schemas.enums import MessageType, SourceKind
from apps.api.schemas.envelope import InboundTurn, OutboundAction
from apps.api.schemas.message import MessageEnvelope
from apps.api.tests.test_booking_journey import (
    _DERMACLUB_COPY,
    CAIRO,
    CLINICS,
    CONVERSATION,
    NOW,
    TENANT_ID,
    _InjectableDirectory,
    _PrimelaseDirectory,
    _turn,
)

# ── A canned provider: one valid Egyptian-Arabic phrasing per eligible act ────────────────────
#
# Each carries a distinctive marker ("يا قمر" / "يا فندم") absent from the deterministic fallback,
# so a test can prove the *generated* sentence reached the patient rather than the template. Every
# template obeys the fact lock: it uses only the act's placeholders, types no digit itself, is pure
# Arabic, makes no clinical claim, and claims the booking is done only on ``booking_confirmed``.

#: The first approved skeleton for each act — a cooperative model that phrases within the lock. Each
#: carries a marker ("يا قمر" / "يا فندم") absent from the deterministic fallback, so a test can
#: prove the generated sentence reached the patient.
_VALID_TEMPLATES: dict[str, str] = {act: exemplars[0] for act, exemplars in _EXEMPLARS.items()}

#: Adversarial phrasings that pass the old digit/English/denylist checks but assert protected facts
#: in prose: a fabricated service/branch/day beside a valid offer, an efficacy claim, and a
#: premature "the booking is confirmed" on a read-back. Each MUST be rejected → deterministic
#: fallback. These are the Codex production-seam probes, driven through the real receptionist below.
_ADVERSARIAL_TEMPLATES: dict[str, str] = {
    "ask_missing_slot": "تمام، تحبي تحجزي البوتوكس في الزمالك؟",
    "offer_times": "البوتوكس في الزمالك يوم الخميس مضمون ونتيجته ممتازة، المتاح {times}",
    "nothing_free": "مفيش مواعيد للبوتوكس في الزمالك يوم الخميس، تحبي تاني؟",
    "read_back": "ميعادك اتأكد لـ {service} في {branch} يوم {date} الساعة {time}",
    "booking_confirmed": "تم حجز البوتوكس في الزمالك، رقم حجزك {booking_reference}",
}


class _CannedProvider:
    """Returns a phrasing for the spec's act and counts every call it receives."""

    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self._templates = templates if templates is not None else dict(_VALID_TEMPLATES)
        self.calls: list[str] = []

    def complete(self, spec: RenderSpec) -> str:
        self.calls.append(spec.act)
        return self._templates[spec.act]


@pytest.fixture
def provider() -> _CannedProvider:
    return _CannedProvider()


@pytest.fixture
def generative(provider: _CannedProvider) -> Iterator[_CannedProvider]:
    """Wire the process renderer to a generative renderer over the canned provider."""
    configure_renderer(GenerativeRenderer(provider))
    try:
        yield provider
    finally:
        configure_renderer(TemplateRenderer())  # never leak generative mode into other tests


@pytest.fixture
def adversarial() -> Iterator[_CannedProvider]:
    """A generative renderer over a provider that tries to smuggle fabricated facts in prose."""
    provider = _CannedProvider(dict(_ADVERSARIAL_TEMPLATES))
    configure_renderer(GenerativeRenderer(provider))
    try:
        yield provider
    finally:
        configure_renderer(TemplateRenderer())


def _wire(directory: ClinicDirectory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the four booking tools + greet/close against a diary, in DermaClub Arabic."""
    copy = _DERMACLUB_COPY
    for tool in (
        Greet(copy),
        CloseConversation(copy),
        CheckAvailability(directory, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        QuotePrice(directory, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        HoldSlot(directory, timezone=CAIRO, copy=copy, clock=lambda: NOW),
        ConfirmBooking(
            directory, timezone=CAIRO, reference_prefix="DC", copy=copy, clock=lambda: NOW
        ),
    ):
        monkeypatch.setitem(REGISTRY, tool.name, tool)
    monkeypatch.setattr(tools, "_COPY", copy)


@pytest.fixture
def dermaclub(monkeypatch: pytest.MonkeyPatch) -> _PrimelaseDirectory:
    fake = _PrimelaseDirectory()
    _wire(fake, monkeypatch)
    return fake


@pytest.fixture
def injectable(monkeypatch: pytest.MonkeyPatch) -> _InjectableDirectory:
    fake = _InjectableDirectory()
    _wire(fake, monkeypatch)
    return fake


def _say(
    text: str,
    intent: str,
    slots: dict[str, str] | None = None,
    task: Task | None = None,
    turns_taken: int = 0,
) -> tuple[OutboundAction, Task]:
    return asyncio.run(
        handle(
            _turn(text),
            intent,
            0.95,
            slots or {},
            task,
            vocabulary=CLINICS,
            conversation_id=CONVERSATION,
            turns_taken=turns_taken,
        )
    )


# ── Journey A: the full concrete booking, through generative phrasing ─────────────────────────


def test_the_full_booking_journey_sounds_generated_but_books_deterministically(
    dermaclub: _PrimelaseDirectory, generative: _CannedProvider
) -> None:
    """The demo script, turn by turn, in generative mode.

        صباح الخير / عايزة احجز / برايم ليز جلسة واحدة / المعادي / بكرة
        → real availability → chosen time → natural read-back → explicit yes → durable reference

    Every eligible act is phrased by the model (each generated sentence carries its marker), while
    the times, the day and the reference come only from the deterministic layer. Exactly one real
    booking is written and ``DC-0266`` comes back.
    """
    greet, _ = _say("صباح الخير", "greeting", {}, None)
    assert greet.kind == "say"  # greeting is not an eligible act; deterministic

    ask_service, task = _say("عايزة احجز", "booking_enquiry", {}, None, turns_taken=0)
    assert ask_service.kind == "ask"
    assert "يا فندم" in (ask_service.text or "")  # ask_missing_slot was generated

    ask_branch, task = _say(
        "برايم ليز جلسة واحدة",
        "booking_enquiry",
        {"service": "برايم ليز", "session_count": "1"},
        task,
        turns_taken=1,
    )
    assert ask_branch.kind == "ask"
    assert "يا فندم" in (ask_branch.text or "")

    ask_date, task = _say("المعادي", "booking_enquiry", {"branch": "المعادي"}, task, turns_taken=2)
    assert ask_date.kind == "ask"
    assert "يا فندم" in (ask_date.text or "")

    offer, task = _say(
        "بكرة", "booking_enquiry", {"requested_date": "2026-09-02"}, task, turns_taken=3
    )
    assert offer.kind == "ask"
    # Generated phrasing, but the diary's own times, substituted — never invented by the model.
    assert "يا قمر" in (offer.text or "")
    assert "17:00" in (offer.text or "") and "18:00" in (offer.text or "")

    read_back, task = _say("الساعة ٥", "unclear", {}, task, turns_taken=4)
    assert read_back.kind == "confirm"
    assert "يا قمر" in (read_back.text or "")
    assert "17:00" in (read_back.text or "")
    assert read_back.quick_replies == ["أيوه", "لأ"]

    booked, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=5)
    assert booked.kind == "say"
    assert "يا قمر" in (booked.text or "")  # booking_confirmed was generated
    assert "DC-0266" in (booked.text or "")  # the durable reference, from the diary

    assert [b.reference for b in dermaclub.bookings] == ["DC-0266"]
    assert task.slots["booking_reference"] == "DC-0266"
    # One call per eligible act on the journey: three asks, one offer, one read-back, one confirm.
    assert generative.calls == [
        "ask_missing_slot",
        "ask_missing_slot",
        "ask_missing_slot",
        "offer_times",
        "read_back",
        "booking_confirmed",
    ]


def test_nothing_free_is_generated_and_names_no_invented_slot(
    dermaclub: _PrimelaseDirectory, generative: _CannedProvider
) -> None:
    """A day with nothing free is an eligible act — phrased, but offering no fabricated time."""
    action, task = _say(
        "احجزيلي برايم ليز جلسة واحدة في المعادي الخميس",
        "booking_enquiry",
        {
            "service": "برايم ليز",
            "session_count": "1",
            "branch": "المعادي",
            "requested_date": "2026-09-03",
        },
    )
    assert action.kind == "ask"
    assert "يا فندم" in (action.text or "")
    assert generative.calls == ["nothing_free"]
    assert dermaclub.bookings == []


def test_a_missing_slot_question_asks_about_the_slot_the_task_chose(
    dermaclub: _PrimelaseDirectory, generative: _CannedProvider
) -> None:
    """Blocker 2: the model is handed the deterministic ``{slot}`` descriptor, not a free choice.

    The service ask must name the service, the branch ask the branch — the model cannot ask for a
    branch when the task is still missing a service, because the descriptor it substitutes is the
    slot ``next_step()`` returned.
    """
    ask_service, task = _say("عايزة احجز", "booking_enquiry", {}, None, turns_taken=0)
    assert "الخدمة اللي تحبي تحجزيها" in (ask_service.text or "")
    assert "الفرع" not in (ask_service.text or "")

    ask_branch, task = _say(
        "برايم ليز جلسة واحدة",
        "booking_enquiry",
        {"service": "برايم ليز", "session_count": "1"},
        task,
        turns_taken=1,
    )
    assert "الفرع اللي يناسبك" in (ask_branch.text or "")


# ── The Codex production-seam probes: fabricated facts in prose fall back to deterministic ─────


def test_a_fabricated_offer_in_prose_falls_back_to_the_deterministic_offer(
    dermaclub: _PrimelaseDirectory, adversarial: _CannedProvider
) -> None:
    """The real receptionist path: a model naming بوتوكس/الزمالك/الخميس is rejected, not delivered.

    The patient sees the deterministic Arabic offer with the diary's real service, branch and
    times — never the fabricated botox-in-Zamalek-on-Thursday sentence.
    """
    _, task = _say("عايزة احجز", "booking_enquiry", {}, None, turns_taken=0)
    _, task = _say(
        "برايم ليز جلسة واحدة",
        "booking_enquiry",
        {"service": "برايم ليز", "session_count": "1"},
        task,
        turns_taken=1,
    )
    _, task = _say("المعادي", "booking_enquiry", {"branch": "المعادي"}, task, turns_taken=2)
    offer, task = _say(
        "بكرة", "booking_enquiry", {"requested_date": "2026-09-02"}, task, turns_taken=3
    )
    assert "بوتوكس" not in (offer.text or "") and "الزمالك" not in (offer.text or "")
    assert "الخميس" not in (offer.text or "") and "مضمون" not in (offer.text or "")
    # The deterministic DermaClub offer, with the real diary values.
    assert "متاح عندنا" in (offer.text or "")
    assert "17:00" in (offer.text or "") and "18:00" in (offer.text or "")


def test_a_premature_confirmation_paraphrase_falls_back_and_books_nothing(
    dermaclub: _PrimelaseDirectory, adversarial: _CannedProvider
) -> None:
    """A read-back that tries to say "ميعادك اتأكد" falls back — and the diary is untouched.

    The patient is asked to confirm, not told it is done, and no booking exists before the yes.
    """
    task = Task(
        intent="booking_enquiry",
        slots={
            "service": "برايم ليز",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
            "requested_time": "17:00",
        },
        vocabulary=CLINICS,
    )
    read_back, task = _say(
        "عاوزة أحجز برايم ليز جلسة واحدة في المعادي بكرة الساعة ٥",
        "booking_enquiry",
        {
            "service": "برايم ليز",
            "session_count": "1",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
            "requested_time": "17:00",
        },
    )
    assert read_back.kind == "confirm"
    assert "اتأكد" not in (read_back.text or "")  # never claims the booking is done
    assert (read_back.text or "").startswith("تأكيد الحجز:")  # the deterministic read-back
    assert dermaclub.bookings == []  # explicit-confirmation invariant intact


def test_a_fabricated_confirmation_still_delivers_the_real_reference_deterministically(
    dermaclub: _PrimelaseDirectory, adversarial: _CannedProvider
) -> None:
    """Even the confirmation act, if it names a fabricated service, falls back — reference kept."""
    task = Task(
        intent="booking_enquiry",
        slots={
            "service": "برايم ليز",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
            "requested_time": "17:00",
        },
        vocabulary=CLINICS,
    )
    booked, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=1)
    assert booked.kind == "say"
    assert "بوتوكس" not in (booked.text or "") and "الزمالك" not in (booked.text or "")
    assert "DC-0266" in (booked.text or "")  # the real durable reference, deterministic wording
    assert [b.reference for b in dermaclub.bookings] == ["DC-0266"]


# ── Journey D + the excluded surfaces: the renderer never runs on a safety path ───────────────


def test_a_clinical_block_never_invokes_the_renderer(
    injectable: _InjectableDirectory, generative: _CannedProvider
) -> None:
    """A screened treatment hands off at catalogue resolution — before any render call."""
    action, task = _say(
        "في ميعاد فيلر في المعادي بكرة؟",
        "availability_check",
        {"service": "فيلر", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    assert action.kind == "handoff"
    assert task.status is TaskStatus.HANDED_OFF
    assert action.text == _DERMACLUB_COPY.handoff  # deterministic safety wording, untouched
    assert generative.calls == []


def test_a_pregnancy_disclosure_never_invokes_the_renderer(
    dermaclub: _PrimelaseDirectory, generative: _CannedProvider
) -> None:
    action, task = _say(
        "أنا حامل، في ميعاد برايم ليز جلسة واحدة في المعادي بكرة؟",
        "availability_check",
        {
            "service": "برايم ليز",
            "session_count": "1",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
        },
    )
    assert action.kind == "handoff"
    assert generative.calls == []
    assert dermaclub.bookings == []


def test_the_generic_handoff_never_invokes_the_renderer(generative: _CannedProvider) -> None:
    """Repeated non-progress reaches the hand-off boundary — a deterministic surface."""
    action, task = _say("؟", "availability_check", {}, None, turns_taken=5)
    assert action.kind == "handoff"
    assert generative.calls == []


def test_the_unbuilt_fallback_never_invokes_the_renderer(
    monkeypatch: pytest.MonkeyPatch, generative: _CannedProvider
) -> None:
    """With the clinic tools absent, a booking hands off — and never reaches booking_confirmed."""
    monkeypatch.setattr(tools, "_COPY", _DERMACLUB_COPY)
    assert "check_availability" not in REGISTRY
    task = Task(
        intent="availability_check",
        slots={"service": "برايم ليز", "branch": "المعادي", "requested_date": "2026-09-02"},
        confirmed={"service", "branch", "requested_date"},
        vocabulary=CLINICS,
    )
    action, task = _say("المواعيد المتاحة", "availability_check", {}, task)
    assert action.kind == "handoff"
    assert action.text == _DERMACLUB_COPY.unbuilt
    assert generative.calls == []


def test_a_price_quote_never_invokes_the_renderer(
    dermaclub: _PrimelaseDirectory, generative: _CannedProvider
) -> None:
    """Price is never eligible: the deterministic quote stands, exact to the last digit."""
    action, _task = _say(
        "الباكدج الست جلسات بكام؟", "price_enquiry", {"service": "برايم ليز 6 جلسات"}
    )
    assert action.kind == "say"
    assert "15,000 EGP" in (action.text or "")
    assert generative.calls == []


def test_an_ambiguous_service_never_invokes_the_renderer(
    dermaclub: _PrimelaseDirectory, generative: _CannedProvider
) -> None:
    """A "which did you mean?" is a catalogue disambiguation, not an eligible act."""
    action, _task = _say(
        "في مواعيد لليزر ١٢ جلسة في المعادي بكرة؟",
        "availability_check",
        {"service": "laser 12", "branch": "المعادي", "requested_date": "2026-09-02"},
    )
    assert action.kind == "ask"
    assert "Laser Full Body 12 Sessions" in (action.text or "")
    assert generative.calls == []


# ── Booking confirmation requires a real durable reference ────────────────────────────────────


def test_booking_confirmed_is_only_generated_once_a_real_reference_exists(
    monkeypatch: pytest.MonkeyPatch, generative: _CannedProvider
) -> None:
    """Without the booking tool there is no reference, so no ``booking_confirmed`` generation.

    The confirmation phrasing is reachable only from ``_book`` after the scheduling system returns
    a durable reference; a task that agrees but cannot book hands off, and the renderer is never
    asked to phrase a confirmation it cannot prove.
    """
    monkeypatch.setattr(tools, "_COPY", _DERMACLUB_COPY)
    assert "confirm_booking" not in REGISTRY
    task = Task(
        intent="booking_enquiry",
        slots={
            "service": "برايم ليز",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
            "requested_time": "17:00",
        },
        vocabulary=CLINICS,
    )
    action, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=1)
    assert action.kind == "handoff"
    assert "booking_reference" not in task.slots
    assert "booking_confirmed" not in generative.calls


# ── The persistence invariant: generated text is persisted, delivered, and returned ───────────


class _FakeAudit:
    def write(self, entry: object) -> None:  # noqa: D401 - test stub
        return None


class _FakeInbox:
    def create(self, draft: object) -> None:
        return None


class _RecordingConversations:
    """Records what ``record_reply`` persisted, so it can be compared to what was delivered."""

    def __init__(self, task: Task) -> None:
        self.task: Task | None = task
        self.replies: list[OutboundAction] = []

    def begin(self, turn: InboundTurn) -> ConversationState:
        return ConversationState(conversation_id=CONVERSATION, task=self.task, replies_sent=1)

    def record_reply(
        self, state: ConversationState, turn: InboundTurn, task: Task | None, action: OutboundAction
    ) -> None:
        if task is not None:
            self.task = task
        self.replies.append(action)


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[OutboundAction] = []

    async def send(self, action: OutboundAction, turn: InboundTurn) -> None:
        self.sent.append(action)

    def close(self) -> None:
        return None


class _ScriptedProvider:
    def __init__(self, model_id: str, response: dict[str, object]) -> None:
        self.model_id = model_id
        self._response = response

    def complete_json(self, value: object) -> dict[str, object]:
        return self._response


def _confirmation_classification() -> dict[str, object]:
    return {
        "intent": "thanks_closing",
        "summary_one_line": "yes",
        "language": "ar",
        "person_name": None,
        "company_name": None,
        "extracted_slots": {},
        "confidence_overall": 0.95,
        "confidence_intent": 0.95,
        "confidence_person": 0.95,
        "confidence_company": 0.95,
    }


def test_the_accepted_generated_text_is_exactly_what_is_persisted_and_delivered(
    dermaclub: _PrimelaseDirectory, generative: _CannedProvider
) -> None:
    """Generation happens before persistence, so all three copies are the same object.

    The receptionist returns one ``OutboundAction`` carrying the generated confirmation; the
    orchestrator persists that object, delivers it, and returns it on the outcome — never a
    template sentence recorded and a different generated one sent.
    """
    complete = Task(
        intent="booking_enquiry",
        slots={
            "service": "برايم ليز",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
            "requested_time": "17:00",
        },
        vocabulary=CLINICS,
    )
    store = _RecordingConversations(complete)
    sender = _RecordingSender()
    classifier = Classifier(
        _ScriptedProvider("cheap", _confirmation_classification()),
        _ScriptedProvider("big", _confirmation_classification()),
    )
    orch = Orchestrator(
        classifier,
        _FakeAudit(),
        _FakeInbox(),
        crm_lookup=lambda _t, _c: [],
        receptionist=receptionist_handle,
        conversations=store,
        sender=sender,
        vocabulary=CLINICS,
        policy=DEFAULT_POLICY,
    )
    message = MessageEnvelope(
        external_id="wamid.confirm",
        thread_id="201000000000",
        source_kind=SourceKind.DIRECT,
        sender_phone_e164="+201000000000",
        type=MessageType.TEXT,
        body_text="أيوه",
        received_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    )
    outcome = asyncio.run(orch.process(str(TENANT_ID), "msg-confirm", message))

    assert outcome.action is RoutingAction.RECEPTIONIST_REPLY
    assert outcome.outbound_action is not None
    text = outcome.outbound_action.text
    assert "يا قمر" in text and "DC-0266" in text  # the generated confirmation, not the template
    # The one object down all three paths: persisted history, WhatsApp delivery, the outcome.
    assert store.replies[-1] is outcome.outbound_action
    assert sender.sent[-1] is outcome.outbound_action
    assert store.replies[-1].text == sender.sent[-1].text == text
    assert [b.reference for b in dermaclub.bookings] == ["DC-0266"]


# ── Template mode stays the deterministic default ─────────────────────────────────────────────


def test_template_mode_makes_no_provider_calls(
    dermaclub: _PrimelaseDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default renderer is the no-op one: the journey runs with zero model calls.

    An exploding provider stands wired behind a *generative* renderer would raise on any call — but
    the configured renderer is the ``TemplateRenderer``, which never reaches a provider, so the
    whole booking completes on deterministic Arabic and nothing is generated.
    """
    configure_renderer(TemplateRenderer())
    assert isinstance(renderer_module.current_renderer(), TemplateRenderer)

    offer, task = _say(
        "عاوزة أحجز برايم ليز جلسة واحدة في المعادي بكرة",
        "booking_enquiry",
        {
            "service": "برايم ليز",
            "session_count": "1",
            "branch": "المعادي",
            "requested_date": "2026-09-02",
        },
    )
    assert offer.kind == "ask"
    # The deterministic offer, with the tenant's own template — no generated marker.
    assert "متاح عندنا" in (offer.text or "")
    assert "يا قمر" not in (offer.text or "")
    assert "17:00" in (offer.text or "")

    read_back, task = _say("الساعة ٥", "unclear", {}, task, turns_taken=1)
    booked, task = _say("أيوه", "thanks_closing", {}, task, turns_taken=2)
    assert "DC-0266" in (booked.text or "")
    assert "يا قمر" not in (booked.text or "")
