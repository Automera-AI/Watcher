"""Tool registry for the receptionist (ported from the v2 scaffold, roadmap 1.2).

A tool is something the receptionist is allowed to *do* — take a message, hand off to a human.
The registry validates tool names against the vocabulary so a tool cannot exist without a
declared intent knowing about it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from packages.intents.schema import Vocabulary, default_vocabulary

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
