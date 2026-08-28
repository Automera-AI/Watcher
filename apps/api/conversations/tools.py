"""Tool registry for the receptionist (ported from the v2 scaffold, roadmap 1.2).

A tool is something the receptionist is allowed to *do* — take a message, hand off to a human.
The registry validates tool names against the vocabulary so a tool cannot exist without a
declared intent knowing about it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.clinic.catalogue import resolve_branch, resolve_service
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

    closing_booking_confirmed: str | None = None
    """Said only when a durable booking reference exists. Contains ``{booking_reference}``.

    **This is the one piece of copy that can lie.** It says a booking is confirmed, so it may only
    ever be rendered when the scheduling system has actually returned a reference — see
    ``CloseConversation.run``, which falls back to ``closing`` whenever it has no reference to put
    in it. Nothing in the receptionist supplies one until ``confirm_booking`` is built, so today
    this text is unreachable, which is the correct behaviour rather than a gap.
    """


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


def _fill_all(template: str, **fields: str) -> str | None:
    """Substitute several fields, or ``None`` if the template cannot take them.

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
        template = self._copy.choose_one or "Which did you mean: {options}?"
        return ToolResult(
            ok=False,
            error="ambiguous",
            data={"options": list(options)},
            human_summary=_fill_all(template, options=listed) or f"Which did you mean: {listed}?",
        )

    def _resolve(self, tenant_id: str, service: str, branch: str | None) -> ToolResult | _Resolved:
        """The service and branch a patient named, or the ``ToolResult`` that has to be said."""
        services = self._directory.list_services(tenant_id)
        found_service = resolve_service(service, services)
        if found_service.ambiguous:
            return self._choose([s.name for s in found_service.candidates])
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

        resolved = self._resolve(tenant_id, service_text, branch_text)
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
            template = (
                self._copy.availability_none
                or "I'm sorry — there is nothing free for {service} at {branch} on {date}."
            )
            return ToolResult(
                ok=False,
                error="none_available",
                data={"times": [], "service_category": resolved.service.category},
                human_summary=_fill_all(
                    template,
                    service=resolved.service.name,
                    branch=resolved.branch.name,
                    date=spoken_date,
                )
                or f"There is nothing free on {spoken_date}.",
            )

        times = [self._local(slot.starts_at).strftime("%H:%M") for slot in slots]
        listed = " / ".join(times)
        template = (
            self._copy.availability_offer
            or "I can offer {times} for {service} at {branch} on {date}. Which would you like?"
        )
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
            human_summary=_fill_all(
                template,
                times=listed,
                service=resolved.service.name,
                branch=resolved.branch.name,
                date=spoken_date,
            )
            or f"I can offer {listed} on {spoken_date}. Which would you like?",
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

        resolved = self._resolve(tenant_id, service_text, None)
        if isinstance(resolved, ToolResult):
            return resolved
        service = resolved.service

        template = self._copy.price_quote or (
            "{service}: {price} for {sessions}. Package use is subject to the clinic's "
            "written terms."
        )
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
            human_summary=_fill_all(
                template,
                service=service.name,
                price=price,
                currency=service.currency,
                sessions=sessions,
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
            template = (
                self._copy.booking_taken
                or "I'm sorry — that time has just been taken. Shall I look at what else is free?"
            )
            return ToolResult(
                ok=False,
                error=outcome.reason,
                human_summary=_fill_all(template) or template,
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


def configure_conversation_copy(copy: ConversationCopy) -> None:
    """Swap in the tenant's own opening and closing wording (``main.py``).

    Mirrors ``configure_knowledge``: before this is called the tools are wired to neutral wording
    that greets and closes correctly but names nobody, which is the same degrade-don't-crash trade
    ``_NoFacts`` makes for a tenant with no facts.
    """
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
