"""Tool registry for the receptionist (ported from the v2 scaffold, roadmap 1.2).

A tool is something the receptionist is allowed to *do* — take a message, hand off to a human.
The registry validates tool names against the vocabulary so a tool cannot exist without a
declared intent knowing about it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.clinic.catalogue import (
    ServiceMatch,
    resolve_branch,
    resolve_branch_in_message,
    resolve_service,
    resolve_service_in_message,
)
from apps.api.core.clinic import Branch, ClinicDirectory, Service
from apps.api.core.knowledge import Fact, KnowledgeLookup, best_match
from apps.api.core.property import PropertyResolver


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of running a tool."""

    ok: bool
    data: dict[str, Any] | None = None
    human_summary: str | None = None
    error: str | None = None


class Tool(ABC):
    """A capability the receptionist can invoke."""

    name: str
    description: str
    requires_verified_identity: bool = False
    budget_ms: int = 5000

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult: ...


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


class TakeMessage(Tool):
    """Record the guest's message for follow-up."""

    name = "take_message"
    description = "Record a message when no immediate action is needed."

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(ok=True, human_summary="Message noted for follow-up.")


class HandoffToHuman(Tool):
    """Transfer the conversation to a human operator."""

    name = "handoff_to_human"
    description = "Escalate to a person when the receptionist cannot handle the request."

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(ok=True, human_summary="Transferred to a team member.")


#: The placeholder a tenant may put in its named opening, and in its confirmed-booking closing.
_NAME_FIELD = "customer_name"
_REFERENCE_FIELD = "booking_reference"


def _fill(template: str, field: str, value: str) -> str | None:
    """Substitute one field into a tenant-authored template, or return ``None`` if it cannot.

    Tenant copy is data written by a person who is not looking at this code, so a template can
    carry a typo — ``{booking_ref}`` where the field is ``booking_reference``. ``str.format`` would
    raise ``KeyError`` mid-conversation. Every caller here treats ``None`` as "use the safe
    wording instead", which turns a copy typo into a slightly plainer message rather than a
    customer receiving nothing at all.
    """
    try:
        return template.format(**{field: value})
    except (KeyError, IndexError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ConversationCopy:
    """A tenant's own wording for the two ends of a conversation.

    Every field is optional and every one defaults to neutral English that names nobody. That is
    not a placeholder to be tidied away later — it is what a tenant which has configured nothing
    must still say, and it has to be correct on its own. The client's real lines are configuration
    because they carry the client's name, its receptionist's name and its brand voice, none of
    which belong in shared code (``tests/test_no_client_name.py``).

    ``opening_named`` is a *separate whole sentence* rather than the plain opening with a name
    glued on. The two are not the same message in every language: a tenant writing in Egyptian
    Arabic composes one sentence that happens to contain the name, and prefixing an English
    "Hello Rana!" to it would produce a bilingual mess no client would approve. Whole sentences
    also mean the client reviews exactly what its customers will read.
    """

    opening: str | None = None
    """Said when no customer name is known. A complete message."""

    opening_named: str | None = None
    """Said when a name is known. A complete message containing ``{customer_name}``."""

    closing: str | None = None
    """Said when the customer is done and no booking was confirmed in this conversation."""

    availability_offer: str | None = None
    """Offering real slots. ``{times}``, ``{service}``, ``{branch}``, ``{date}``.

    The times in it come from the scheduling system on this turn and from nowhere else — the clinic
    vocabulary's ``availability_check`` forbids offering a slot the system did not return, and
    ``quoting.max_age_seconds`` is 300, so a list carried over from an earlier turn is already
    stale by the time a patient replies to it.
    """

    availability_none: str | None = None
    """Nothing free. ``{service}``, ``{branch}``, ``{date}``."""

    price_quote: str | None = None
    """A price. ``{service}``, ``{price}``, ``{currency}``, ``{sessions}``.

    Every one of those placeholders is required by the vocabulary's ``quoting.always_state``: the
    currency, whether the amount covers one session or a package, and the quantity when there is
    one. A tenant template that drops the session count turns "15,000 EGP for six" into "15,000
    EGP", which is the same number meaning a fifth as much treatment.
    """

    choose_one: str | None = None
    """Asking which of several catalogue items was meant. ``{options}``.

    Reached when a patient's words match more than one — "Basic Facial" and "Facial" are both 750
    for 45 minutes. Asking is the only safe answer: the price would be right and the appointment
    would be for something they did not ask for.
    """

    booking_taken: str | None = None
    """The slot went while the patient was answering. ``{service}``, ``{branch}``, ``{date}``."""

    ask_service: str | None = None
    """The question asked when a booking is missing only its service. ``{branch}``, ``{date}``.

    Composed by the receptionist rather than a tool, like the read-back and the confirmation, and
    for the same reason it was the last English leak on the demo turns: the patient names a branch
    and a day but no treatment, and the generic "Could you please provide the service?" answered
    that in English, context-blind, in the middle of an otherwise-configured Arabic conversation.

    Both placeholders are *pre-composed fragments*, not raw values: the receptionist fills them
    with the branch and day it already holds — "في فرع المعادي", "بكرة" — including the connective
    words, and with an empty string when that detail is not known yet, so the one template reads
    correctly whether it has both, one, or neither. A tenant overriding this writes the sentence
    around ``{branch}{date}`` and lets those fragments carry whatever context the turn has.
    """

    confirm_read_back: str | None = None
    """The read-back before an appointment is written. ``{details}``, ``{values}``.

    Said by the receptionist rather than by a tool, which is why it took until the journey was run
    end to end for anyone to notice it had no tenant wording at all: two of the demo's three turns
    were composed in English inside ``receptionist.py`` while every sentence around them came from
    configuration.

    Two placeholders for the same list because the labels are the problem. ``{details}`` is what
    the default reads — "service Facial, branch Maadi" — and those labels are English words that
    have no business inside an Arabic sentence. ``{values}`` is the same details with the labels
    dropped, which is what a template in another language wants: a booking read back as
    "فاشيال، المعادي، Wednesday 02 September، 19:00" is understood by the patient answering it.
    """

    booking_confirmed: str | None = None
    """Said when the appointment has been written. Contains ``{booking_reference}``.

    Distinct from ``closing_booking_confirmed``, which is the *goodbye* afterwards. This is the
    sentence that states the appointment exists, and like that one it may only be rendered when a
    reference came back from the scheduling system — the receptionist has nothing else to put in
    it, and a confirmation without one is the "All set!" bug wearing a better sentence.
    """

    closing_booking_confirmed: str | None = None
    """Said only when a durable booking reference exists. Contains ``{booking_reference}``.

    **This is the one piece of copy that can lie.** It says a booking is confirmed, so it may only
    ever be rendered when the scheduling system has actually returned a reference — see
    ``CloseConversation.run``, which falls back to ``closing`` whenever it has no reference to put
    in it. Nothing in the receptionist supplies one until ``confirm_booking`` is built, so today
    this text is unreachable, which is the correct behaviour rather than a gap.
    """

    ask_branch: str | None = None
    """The question asked when a booking is missing only its branch. No placeholders.

    Composed by the receptionist, like ``ask_service``, and the branch/date/time asks are the ones
    the step-by-step booking journey hits between the service and the diary — ``برايم ليز`` then
    ``المعادي`` then ``بكرة`` is three separate turns, and each of the second and third used to be
    answered by the generic English ``Could you please provide the …?``. The Arabic default lives in
    ``receptionist.py`` (``_ASK_BRANCH_TEXT``); this seam only lets a tenant set the phrasing in its
    own voice. Only reached for the clinic booking slots — a vertical whose bookings ask for other
    slots keeps the generic prompt (``receptionist._ask_for_slot``)."""

    ask_date: str | None = None
    """The question asked when a booking is missing only its date. No placeholders. See
    ``ask_branch``; Arabic default ``receptionist._ASK_DATE_TEXT``."""

    ask_time: str | None = None
    """The question asked when a booking is missing only its time and one is asked for directly. No
    placeholders. On the demo flow the time is *offered* rather than asked (``check_availability``),
    so this is the rarely-reached ask; it exists so the fallback is Arabic if it is. Arabic default
    ``receptionist._ASK_TIME_TEXT``."""

    handoff: str | None = None
    """The generic hand-off sentence: said whenever a person is fetched — the autonomy ceiling, the
    clarification limit, a clinical block, an unresolvable tool. **A safety surface**: the renderer
    never phrases it (plan §8), so this deterministic wording is the only thing a patient reads when
    the conversation leaves autonomy. The in-code default (``receptionist.HANDOFF_TEXT``) is neutral
    English that names nobody; a clinic sets it in Arabic so its own hand-offs are not the one
    English sentence in an Arabic conversation. No placeholders."""

    unbuilt: str | None = None
    """Said when a capability is not built yet: the message is recorded and a person is fetched.
    Like ``handoff`` this is a safety surface the renderer never owns, and its in-code default
    (``receptionist._UNBUILT_TEXT``) is neutral English. No placeholders."""

    clarify_change: str | None = None
    """Asked after a read-back is declined: which detail to change. Reached on the booking journey
    when a patient answers the confirmation with "no", so a clinic wants it in Arabic; the in-code
    default (``receptionist._CLARIFY_CHANGE_TEXT``) is neutral English. No placeholders."""

    confirm_yes: str | None = None
    """The affirmative quick-reply button shown with a read-back. The reply text is already the
    tenant's (``confirm_read_back``), and the two buttons beside it were the last English on the
    read-back turn — "Yes" / "No" under an Arabic question. A clinic sets these to "أيوه" / "لأ",
    which ``conversations/confirmation.py`` reads as agreement/refusal. Defaults to "Yes"."""

    confirm_no: str | None = None
    """The negative quick-reply button shown with a read-back. See ``confirm_yes``. Defaults to
    "No"."""


class Greet(Tool):
    """Open the conversation: say hello, say what can be done here, invite the request.

    **Why a greeting needs a tool at all.** It did not have one, and the consequence was not a
    missing feature but a wrong answer to the single most common message a receptionist receives.
    "Hi" had two routes and both ended in the same sentence: classified ``unclear``, whose
    ``max_autonomy`` is ``hand_off``, so ``decide_autonomy`` fetched a person before confidence
    was even consulted; or classified ``general_info``, whose ``answer_from_knowledge`` searched
    the facts table for "hi", found nothing, and fell through ``on_no_knowledge`` to the same
    hand-off. A greeting is not a failure to understand, and this is the tool that says so.

    The wording is tenant configuration, not code — see ``ConversationCopy``. Without one
    configured the reply is deliberately plain and still correct: it greets, and it invites the
    request.
    """

    name = "greet"
    description = "Open the conversation and invite the customer's request."

    _FALLBACK = "Hello! How can I help you today?"
    _FALLBACK_NAMED = "Hello {customer_name}! How can I help you today?"

    def __init__(self, copy: ConversationCopy | None = None) -> None:
        self._copy = copy or ConversationCopy()

    async def run(self, **kwargs: Any) -> ToolResult:
        name: str | None = kwargs.get(_NAME_FIELD)
        if name:
            template = self._copy.opening_named or self._FALLBACK_NAMED
            if (greeting := _fill(template, _NAME_FIELD, name)) is not None:
                return ToolResult(ok=True, data={"greeted_by_name": True}, human_summary=greeting)
        return ToolResult(
            ok=True,
            data={"greeted_by_name": False},
            human_summary=self._copy.opening or self._FALLBACK,
        )


class CloseConversation(Tool):
    """Close politely when the customer says thanks or goodbye.

    Paired with ``Greet`` for the same reason: "شكراً" classified as ``unclear`` handed off, which
    turns a *completed* conversation into one that looks unresolved and puts a person in front of
    someone who was only saying goodbye. Nothing is asked here, so nothing is asked back — in
    particular no add-on, which is the temptation and which reads as a sales pitch after the
    customer has already finished.
    """

    name = "close_conversation"
    description = "Close the conversation politely when the customer is done."

    _FALLBACK = "You're very welcome. Have a lovely day!"

    def __init__(self, copy: ConversationCopy | None = None) -> None:
        self._copy = copy or ConversationCopy()

    async def run(self, **kwargs: Any) -> ToolResult:
        """Close, naming the booking reference only when there genuinely is one.

        The confirmed-booking closing states that an appointment exists. Rendering it without a
        reference from the scheduling system would be the "All set! I've noted everything down."
        bug again, in the customer's own language and with more authority — so the reference is
        the *precondition*, not a slot to fill in. No reference, or a template that will not take
        one, and the generic closing is used instead.
        """
        reference: str | None = kwargs.get(_REFERENCE_FIELD)
        template = self._copy.closing_booking_confirmed
        if reference and template:
            if (closing := _fill(template, _REFERENCE_FIELD, reference)) is not None:
                return ToolResult(
                    ok=True,
                    data={"booking_reference": reference},
                    human_summary=closing,
                )
        return ToolResult(ok=True, human_summary=self._copy.closing or self._FALLBACK)


class _NoFacts:
    """The default knowledge lookup: no tenant has any facts. Every match is a real "I don't
    know" rather than a crash — the same trade ``channels/factory.py`` makes for a process with
    no send credentials: a degraded, working state, not a refusal to run."""

    def search(self, tenant_id: str, property_id: str | None = None) -> list[Fact]:
        return []


class AnswerFromKnowledge(Tool):
    """Answer a guest's question from the tenant's facts, or say there is nothing to find.

    ``run`` never raises "not found" — an unmatched question is not an error, it is the normal
    shape of ``intents.yaml``'s ``defaults.on_no_knowledge: handoff_to_human``. What decides
    whether that actually happens is the caller (``conversations/receptionist.py``), because only
    it knows whether a hand-off has anywhere useful to reply from.

    A ``sensitive`` match is treated exactly like no match unless the caller vouches for the
    guest's identity via ``identity_verified``. See ``core/knowledge.py`` for why this goes no
    further than "don't say it" — it is not G1.

    ``properties`` scopes the lookup to one unit (roadmap 2.8). When it is set, the tool resolves
    which property the message is about (``core/property.py``) and asks the knowledge base for that
    property's facts plus the tenant-wide ones; without it — the default until the composition root
    supplies one — every fact is treated as tenant-wide, exactly the single-property behaviour 2.4
    shipped with.
    """

    name = "answer_from_knowledge"
    description = "Look up an answer to a guest's question in the tenant's knowledge base."

    def __init__(
        self, knowledge: KnowledgeLookup, properties: PropertyResolver | None = None
    ) -> None:
        self._knowledge = knowledge
        self._properties = properties

    async def run(self, **kwargs: Any) -> ToolResult:
        tenant_id: str = kwargs["tenant_id"]
        question: str = kwargs.get("question") or ""
        identity_verified: bool = bool(kwargs.get("identity_verified", False))
        property_hint: str | None = kwargs.get("property_hint")

        property_id = (
            self._properties.resolve(tenant_id, hint=property_hint)
            if self._properties is not None
            else None
        )
        facts = self._knowledge.search(tenant_id, property_id)
        match = best_match(question, facts)
        if match is None:
            return ToolResult(ok=False, human_summary="No matching fact was found.")
        if match.sensitive and not identity_verified:
            return ToolResult(
                ok=False,
                human_summary=f"matched {match.topic!r} but it is sensitive and the guest is "
                "not verified",
            )
        return ToolResult(ok=True, data={"topic": match.topic}, human_summary=match.answer)


#: How long a slot is held while the patient is asked to confirm it. Long enough for somebody to
#: read a message and reply, short enough that an abandoned conversation does not take an evening
#: appointment out of the diary. Nothing sweeps expired holds — ``available_slots``
#: reads the expiry — so a hold that is never confirmed simply stops counting.
HOLD_MINUTES = 10


def fill_template(template: str, **fields: str) -> str | None:
    """Substitute several fields, or ``None`` if the template cannot take them.

    Public because ``receptionist.py`` renders the two sentences it composes itself through it.
    A second implementation of "fill this in, and degrade rather than raise" is how the two would
    drift, and the one that drifts is the one nobody notices until a tenant's template throws.

    Same contract as :func:`_fill` and for the same reason: a tenant writing ``{sessions_count}``
    where the field is ``sessions`` must degrade to plainer wording rather than raise ``KeyError``
    in the middle of a booking and lose the patient's reply.
    """
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return None


def _money(minor: int, currency: str) -> str:
    """A price a patient can read. Minor units in, never a float anywhere along the way."""
    major, remainder = divmod(minor, 100)
    return f"{major:,}.{remainder:02d} {currency}" if remainder else f"{major:,} {currency}"


#: Which of several catalogue items the patient meant, in Egyptian Arabic. Reached when the words
#: match more than one row — bare "برايم ليز" is the single session, the six-session package and
#: the twelve-session one, and picking any of them books a treatment the patient did not name, so
#: the only safe answer is to ask. The tenant may replace it (``ConversationCopy.choose_one`` /
#: ``TENANT_CHOOSE_ONE``); the default is Arabic in code, like the missing-service ask
#: (``_ASK_SERVICE_TEXT``), so the clarifying question is asked in the patient's language without
#: any configuration rather than in the English constant it used to fall back to. ``{options}`` is
#: the candidate names joined with " / "; those names stay English, the pre-existing gap the demo
#: does not widen.
_CHOOSE_ONE_TEXT = "تحبي أنهي واحدة فيهم: {options}؟"

#: The availability answers, in Egyptian Arabic, for a clinic that configured no wording of its own.
#: These four tools are registered only for the clinic vertical (``configure_clinic``), so an Arabic
#: default here can never reach a holiday-home reply — it is the same reasoning that lets
#: ``_CHOOSE_ONE_TEXT`` be Arabic in code. A tenant still overrides each through its ``copy`` field;
#: the default is only what the clinic path says when nothing is set, and it must be Arabic so the
#: booking journey does not fall back to English when generation is unavailable (plan §7).
#: ``{times}``/``{service}``/``{branch}``/``{date}`` are filled from the scheduling system on the
#: turn — the service and branch names stay as the catalogue holds them, a pre-existing display-name
#: gap the demo does not widen.
_AVAILABILITY_OFFER_TEXT = (
    "متاح عندنا {times} لـ {service} في فرع {branch} يوم {date}. تحبي أحجزلك إمتى؟"
)
_AVAILABILITY_NONE_TEXT = (
    "معلش، مفيش مواعيد فاضية لـ {service} في فرع {branch} يوم {date}. تحبي أشوفلك يوم تاني؟"
)
_BOOKING_TAKEN_TEXT = "معلش، الميعاد ده اتحجز حالاً. تحبي أشوفلك المتاح؟"


class _ClinicTool(Tool):
    """Shared wiring for the four tools that read the imported catalogue.

    They take the same three collaborators — the directory, the tenant's zone, and its wording —
    and they all begin by turning what the patient said into catalogue rows. That resolution is
    here rather than repeated four times because each of them has to fail the *same* way: an
    ambiguous service is a question in every one of them, and a service that quotes as one thing
    and books as another is the worst version of this bug.
    """

    def __init__(
        self,
        directory: ClinicDirectory,
        *,
        timezone: str,
        copy: ConversationCopy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._directory = directory
        self._timezone = timezone
        self._copy = copy or ConversationCopy()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def _zone(self) -> ZoneInfo:
        return ZoneInfo(self._timezone)

    def _choose(self, options: Sequence[str]) -> ToolResult:
        """Ask which of several catalogue items was meant. Never pick one."""
        listed = " / ".join(options)
        template = self._copy.choose_one or _CHOOSE_ONE_TEXT
        return ToolResult(
            ok=False,
            error="ambiguous",
            data={"options": list(options)},
            human_summary=fill_template(template, options=listed)
            or _CHOOSE_ONE_TEXT.format(options=listed),
        )

    def _resolve(
        self,
        tenant_id: str,
        service: str,
        branch: str | None,
        session_count: str | None = None,
    ) -> ToolResult | _Resolved:
        """The service and branch a patient named, or the ``ToolResult`` that has to be said."""
        services = self._directory.list_services(tenant_id)
        found_service = resolve_service(service, services)
        if found_service.ambiguous:
            narrowed = _by_session_count(found_service.candidates, session_count)
            if narrowed is None:
                return self._choose([s.name for s in found_service.candidates])
            found_service = narrowed
        if found_service.found is None:
            return ToolResult(ok=False, error="unknown_service")

        if branch is None:
            return _Resolved(service=found_service.found, branch=None)
        branches = self._directory.list_branches(tenant_id)
        found_branch = resolve_branch(branch, branches)
        if found_branch.ambiguous:
            return self._choose([b.name for b in found_branch.candidates])
        if found_branch.found is None:
            return ToolResult(ok=False, error="unknown_branch")
        return _Resolved(service=found_service.found, branch=found_branch.found)

    def _local(self, moment: datetime) -> datetime:
        return moment.astimezone(self._zone)


def strip_unsupported_clinic_slots(
    resolved: Mapping[str, str], message: str | None, *, tenant_id: str
) -> dict[str, str]:
    """Drop service/branch values more specific than the current patient message proves.

    This is clinic-only by construction: the configured ``check_availability`` tool owns the
    tenant-scoped directory and therefore the authoritative catalogue.  A vertical without that
    tool keeps its slots unchanged.  Current-message evidence is resolved first and independently;
    only then is the classifier value resolved and compared.  Equal catalogue candidate sets are
    the admissible specificity for a service (one exact SKU or one still-ambiguous family), while a
    branch must resolve to the same single location on both sides.
    """
    guarded = dict(resolved)
    tool = REGISTRY.get("check_availability")
    if not isinstance(tool, _ClinicTool):
        return guarded

    text = message or ""
    service_value = guarded.get("service")
    if service_value is not None:
        services = tool._directory.list_services(tenant_id)
        stated = resolve_service_in_message(text, services)
        claimed = resolve_service(service_value, services)
        claimed_codes = {
            service.code
            for service in ((claimed.found,) if claimed.found is not None else claimed.candidates)
        }
        if stated.found is not None:
            if claimed.found is not None and stated.found.code == claimed.found.code:
                # Both sides already identify the same exact row. Preserve the classifier's
                # supported wording so an Arabic alias does not turn into an English read-back.
                pass
            elif claimed.ambiguous and stated.found.code in claimed_codes:
                # The current message, not classifier specificity, proved this exact catalogue
                # row. Store its canonical name even when the classifier supplied only a broader
                # compatible family and normalization discarded a separate package-count slot.
                guarded["service"] = stated.found.name
                if "session_count" in guarded:
                    guarded["session_count"] = str(stated.found.session_count)
            else:
                guarded.pop("service", None)
                guarded.pop("session_count", None)
        elif claimed.ambiguous:
            narrowed = _by_session_count(claimed.candidates, guarded.get("session_count"))
            if narrowed is not None:
                claimed = narrowed
            claimed_codes = {
                service.code
                for service in (
                    (claimed.found,) if claimed.found is not None else claimed.candidates
                )
            }
        if stated.found is None:
            stated_codes = {service.code for service in stated.candidates}
            if not stated_codes or stated_codes != claimed_codes:
                guarded.pop("service", None)
                guarded.pop("session_count", None)
    elif "session_count" in guarded:
        # A count can refine a service that the same message names; by itself it may be an hour,
        # quantity, or old classifier context and must not narrow an existing service family.
        guarded.pop("session_count", None)

    branch_value = guarded.get("branch")
    if branch_value is not None:
        branches = tool._directory.list_branches(tenant_id)
        stated_branch = resolve_branch_in_message(text, branches)
        claimed_branch = resolve_branch(branch_value, branches)
        if (
            stated_branch.found is None
            or claimed_branch.found is None
            or stated_branch.found.external_id != claimed_branch.found.external_id
        ):
            guarded.pop("branch", None)

    return guarded


def _by_session_count(
    candidates: Sequence[Service], session_count: str | None
) -> ServiceMatch | None:
    """The one candidate with this many sessions, or ``None`` to keep asking.

    The patient said "برايم ليز 6 جلسات" and the model, correctly, split that into a service and a
    quantity — which left the catalogue lookup holding "برايم ليز", three packages that differ only
    by how many sessions they are, and a clarifying question whose answer the patient had already
    given. The count they said is the thing that tells those three apart, so it is used.

    It narrows and never picks: a count matching two rows, or none, still asks. Found by running
    the journeys against real classifications — with the labels written by hand the service name
    arrived whole and this never came up.
    """
    if not session_count:
        return None
    try:
        wanted = int(session_count)
    except ValueError:
        return None
    matched = [s for s in candidates if s.session_count == wanted]
    return ServiceMatch(found=matched[0]) if len(matched) == 1 else None


@dataclass(frozen=True, slots=True)
class _Resolved:
    """A patient's words, turned into catalogue rows. ``branch`` is absent for a price question."""

    service: Service
    branch: Branch | None


class CheckAvailability(_ClinicTool):
    """Offer the slots the diary actually holds, for one service at one branch on one day.

    **Every time in the reply came out of the scheduling system on this turn.** That is the
    vocabulary's first ``never`` for this intent and it is not a style rule: opening hours are not
    availability, a roster is not availability, and a list of times carried over from an earlier
    turn is a promise about a diary that has moved. Nothing here composes a time that was not
    returned by :meth:`ClinicDirectory.available_slots`.

    A slot already held by *this* conversation is still offered to it — see the directory's own
    docstring — because otherwise the hold placed to keep a slot for a patient is what takes it
    away from them.
    """

    name = "check_availability"
    description = "List real bookable slots for a service at a branch on a date."

    async def run(self, **kwargs: Any) -> ToolResult:
        tenant_id: str = kwargs["tenant_id"]
        service_text: str | None = kwargs.get("service")
        branch_text: str | None = kwargs.get("branch")
        on: str | None = kwargs.get("requested_date")
        conversation_id: str | None = kwargs.get("conversation_id")

        if not service_text or not branch_text or not on:
            return ToolResult(ok=False, error="incomplete")
        try:
            wanted = date.fromisoformat(on)
        except ValueError:
            return ToolResult(ok=False, error="unreadable_date")

        resolved = self._resolve(tenant_id, service_text, branch_text, kwargs.get("session_count"))
        if isinstance(resolved, ToolResult):
            return resolved
        assert resolved.branch is not None

        slots = self._directory.available_slots(
            tenant_id,
            service_code=resolved.service.code,
            branch_external_id=resolved.branch.external_id,
            on_date=wanted,
            timezone=self._timezone,
            now=self._clock(),
            conversation_id=conversation_id,
        )
        spoken_date = wanted.strftime("%A %d %B")
        if not slots:
            template = self._copy.availability_none or _AVAILABILITY_NONE_TEXT
            return ToolResult(
                ok=False,
                error="none_available",
                data={"times": [], "service_category": resolved.service.category},
                human_summary=fill_template(
                    template,
                    service=resolved.service.name,
                    branch=resolved.branch.name,
                    date=spoken_date,
                )
                or f"معلش، مفيش مواعيد فاضية يوم {spoken_date}.",
            )

        times = [self._local(slot.starts_at).strftime("%H:%M") for slot in slots]
        listed = " / ".join(times)
        template = self._copy.availability_offer or _AVAILABILITY_OFFER_TEXT
        return ToolResult(
            ok=True,
            data={
                "times": times,
                "slot_ids": [slot.external_id for slot in slots],
                "service_code": resolved.service.code,
                # What the clinical gate reads (demo step 7). Reported by the tool that resolved
                # the service because it is the only thing that knows which catalogue row the
                # patient's words reached, and the gate must not resolve it a second time and
                # possibly differently.
                "service_category": resolved.service.category,
                "branch_external_id": resolved.branch.external_id,
            },
            human_summary=fill_template(
                template,
                times=listed,
                service=resolved.service.name,
                branch=resolved.branch.name,
                date=spoken_date,
            )
            or f"متاح عندنا {listed} يوم {spoken_date}. تحبي أحجزلك إمتى؟",
        )


class QuotePrice(_ClinicTool):
    """Quote from the imported catalogue, with everything the vocabulary requires said out loud.

    ``quoting.always_state`` is four things — the currency, whether the amount is one session or a
    package, the quantity, and that package use is subject to the written terms — and the reason
    the list is that specific is in the catalogue itself: one Primelase session is 3,100 and six
    are 15,000. Both are true prices for "Primelase". A quote that omits which one it is is not
    imprecise, it is wrong by a factor of five, and the patient finds out at the counter.

    Nothing is quoted from the knowledge base or from an earlier turn. A service the current
    catalogue does not list has no price here, and the receptionist fetches a person.
    """

    name = "quote_price"
    description = "Quote the current catalogue price for a service or package."

    async def run(self, **kwargs: Any) -> ToolResult:
        tenant_id: str = kwargs["tenant_id"]
        service_text: str | None = kwargs.get("service")
        if not service_text:
            return ToolResult(ok=False, error="incomplete")

        resolved = self._resolve(tenant_id, service_text, None, kwargs.get("session_count"))
        if isinstance(resolved, ToolResult):
            return resolved
        service = resolved.service

        template = self._copy.price_quote or (
            "{service}: {price} for {sessions}. Package use is subject to the clinic's "
            "written terms."
        )
        # Two placeholders for the same fact, because a template written in Arabic cannot use an
        # English phrase. `{sessions}` reads naturally in the default wording; `{session_count}`
        # is the bare number, which is what a tenant needs when their own language inflects the
        # counted noun — "6 جلسات" and "12 جلسة" differ, and no English phrase substitutes into
        # either. A template must carry one of them: see `_check_price_template`.
        sessions = (
            "one session" if service.session_count == 1 else f"{service.session_count} sessions"
        )
        price = _money(service.price_minor, service.currency)
        return ToolResult(
            ok=True,
            data={
                "service_code": service.code,
                "price_minor": service.price_minor,
                "currency": service.currency,
                "session_count": service.session_count,
            },
            human_summary=fill_template(
                template,
                service=service.name,
                price=price,
                currency=service.currency,
                sessions=sessions,
                session_count=str(service.session_count),
            )
            or f"{service.name}: {price} for {sessions}.",
        )


class HoldSlot(_ClinicTool):
    """Reserve the slot a patient has been offered while they are asked to confirm it.

    Placed at the read-back, not at the offer. Holding everything that was listed would take a
    whole afternoon out of the diary for one browsing patient; holding nothing means the slot a
    patient is currently agreeing to can be given away between the question and the answer.

    Returning ``ok=False`` is ordinary. Somebody else got there first, and the receptionist says
    so and offers what is left — which is why this returns a result rather than raising.
    """

    name = "hold_slot"
    description = "Reserve a slot for this conversation while the patient confirms."

    async def run(self, **kwargs: Any) -> ToolResult:
        tenant_id: str = kwargs["tenant_id"]
        conversation_id: str | None = kwargs.get("conversation_id")
        slot_external_id: str | None = kwargs.get("slot_external_id")
        if not conversation_id or not slot_external_id:
            return ToolResult(ok=False, error="incomplete")

        now = self._clock()
        held = self._directory.hold_slot(
            tenant_id,
            slot_external_id=slot_external_id,
            conversation_id=conversation_id,
            until=now + timedelta(minutes=HOLD_MINUTES),
            now=now,
        )
        return ToolResult(
            ok=held,
            error=None if held else "slot_taken",
            data={"slot_external_id": slot_external_id},
        )


class ConfirmBooking(_ClinicTool):
    """Turn an agreed slot into an appointment, and say the reference out loud.

    **The reference is the whole point.** ``closing_booking_confirmed`` — the one piece of tenant
    copy that can lie — will not render without one, and the reason is this method: until it
    returns, nothing in the system has written an appointment anywhere, and a message saying
    otherwise sends somebody to a clinic that has never heard of them. That was the bug removed in
    step 0, and this is the tool whose absence made it a bug rather than a race.

    Idempotent on (tenant, conversation, slot). A patient who taps send twice, or a webhook
    delivered twice, gets the appointment they already have with the reference they were already
    given.
    """

    name = "confirm_booking"
    description = "Create the appointment for a slot the patient has agreed to."

    def __init__(
        self,
        directory: ClinicDirectory,
        *,
        timezone: str,
        reference_prefix: str,
        copy: ConversationCopy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(directory, timezone=timezone, copy=copy, clock=clock)
        self._prefix = reference_prefix

    async def run(self, **kwargs: Any) -> ToolResult:
        tenant_id: str = kwargs["tenant_id"]
        conversation_id: str | None = kwargs.get("conversation_id")
        slot_external_id: str | None = kwargs.get("slot_external_id")
        if not conversation_id or not slot_external_id:
            return ToolResult(ok=False, error="incomplete")

        outcome = self._directory.confirm_booking(
            tenant_id,
            slot_external_id=slot_external_id,
            conversation_id=conversation_id,
            reference_prefix=self._prefix,
            patient_name=kwargs.get("customer_name"),
            patient_phone=kwargs.get("phone"),
            now=self._clock(),
        )
        if outcome.booking is None:
            template = self._copy.booking_taken or _BOOKING_TAKEN_TEXT
            return ToolResult(
                ok=False,
                error=outcome.reason,
                human_summary=fill_template(template) or template,
            )
        return ToolResult(
            ok=True,
            data={
                "booking_reference": outcome.booking.reference,
                "slot_external_id": outcome.booking.slot_external_id,
                "already_confirmed": outcome.reason == "already_confirmed",
            },
        )


def configure_clinic(
    directory: ClinicDirectory,
    *,
    timezone: str,
    reference_prefix: str,
    copy: ConversationCopy | None = None,
) -> None:
    """Wire the four booking tools to a real catalogue (``orchestration/composition.py``).

    The same named seam as ``configure_knowledge``, and the reason it is a seam rather than a
    constructor argument threaded through the receptionist is unchanged: the registry is a
    process-global service locator, and a worker process that never called this would answer every
    booking request as an unbuilt tool — a hand-off, silently, in the one process that handles the
    messages.

    Called only for a tenant whose vertical has these intents. Without it the four names are simply
    absent from the registry, which is exactly the state the holiday-home vertical wants: an intent
    naming a tool nobody registered hands off, and there is no clinic diary to read.
    """
    register(CheckAvailability(directory, timezone=timezone, copy=copy))
    register(QuotePrice(directory, timezone=timezone, copy=copy))
    register(HoldSlot(directory, timezone=timezone, copy=copy))
    register(
        ConfirmBooking(directory, timezone=timezone, reference_prefix=reference_prefix, copy=copy)
    )


register(TakeMessage())
register(HandoffToHuman())
register(AnswerFromKnowledge(_NoFacts()))
register(Greet())
register(CloseConversation())


#: The tenant's wording, for the two sentences the receptionist says itself. The tools each hold
#: their own copy because they are constructed with one; the receptionist is a module of functions
#: and has nowhere to hold it, so it reads this — the same process-global service-locator pattern
#: ``REGISTRY`` already is, set by the same composition-root call, and defaulting to wording that
#: is correct on its own.
_COPY = ConversationCopy()


def current_copy() -> ConversationCopy:
    """The tenant's wording as last configured (``configure_conversation_copy``)."""
    return _COPY


def configure_conversation_copy(copy: ConversationCopy) -> None:
    """Swap in the tenant's own opening and closing wording (``main.py``).

    Mirrors ``configure_knowledge``: before this is called the tools are wired to neutral wording
    that greets and closes correctly but names nobody, which is the same degrade-don't-crash trade
    ``_NoFacts`` makes for a tenant with no facts.
    """
    global _COPY
    _COPY = copy
    register(Greet(copy))
    register(CloseConversation(copy))


def configure_knowledge(
    knowledge: KnowledgeLookup, properties: PropertyResolver | None = None
) -> None:
    """Swap in a real knowledge lookup once the composition root has one (``main.py``).

    Mirrors how ``register`` already works: the registry is a small, named, module-level service
    locator (A5), not a fresh idea introduced here. Before this is called, ``answer_from_knowledge``
    is wired to ``_NoFacts`` and behaves exactly like a tenant with an empty knowledge base.

    ``properties`` (roadmap 2.8) is the resolver that scopes the lookup to one unit. Optional so a
    process without it keeps 2.4's tenant-wide behaviour rather than failing to wire the tool at
    all — the same degrade-don't-crash trade ``_NoFacts`` makes for a tenant with no facts.
    """
    register(AnswerFromKnowledge(knowledge, properties))


def validate_registry(vocabulary: Vocabulary | None = None) -> list[str]:
    """Return tool names in the registry that the vocabulary does not declare."""
    vocab = vocabulary or default_vocabulary()
    declared = {i.terminal_tool for i in vocab.intents if i.terminal_tool}
    return [name for name in REGISTRY if declared and name not in declared]
