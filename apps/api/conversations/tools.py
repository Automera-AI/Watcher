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


class Greet(Tool):
    """Open the conversation: say hello, say what can be done here, invite the request.

    **Why a greeting needs a tool at all.** It did not have one, and the consequence was not a
    missing feature but a wrong answer to the single most common message a receptionist receives.
    "Hi" had two routes and both ended in the same sentence: classified ``unclear``, whose
    ``max_autonomy`` is ``hand_off``, so ``decide_autonomy`` fetched a person before confidence
    was even consulted; or classified ``general_info``, whose ``answer_from_knowledge`` searched
    the facts table for "hi", found nothing, and fell through ``on_no_knowledge`` to the same
    hand-off. A greeting is not a failure to understand, and this is the tool that says so.

    The wording is tenant configuration, not code. ``opening`` carries the client's own line —
    the receptionist's name, the business name, what it can help with — because a shared default
    that names a client is the hardcoding this repo's own test forbids. Without one configured
    the reply is deliberately plain and still correct: it greets, and it invites the request.
    """

    name = "greet"
    description = "Open the conversation and invite the customer's request."

    def __init__(self, opening: str | None = None) -> None:
        self._opening = opening

    async def run(self, **kwargs: Any) -> ToolResult:
        name: str | None = kwargs.get("customer_name")
        opening = self._opening or "How can I help you today?"
        greeting = f"Hello {name}!" if name else "Hello!"
        return ToolResult(
            ok=True,
            data={"greeted_by_name": bool(name)},
            human_summary=f"{greeting} {opening}",
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

    def __init__(self, closing: str | None = None) -> None:
        self._closing = closing

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(
            ok=True,
            human_summary=self._closing or "You're very welcome. Have a lovely day!",
        )


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


def configure_conversation_copy(opening: str | None, closing: str | None) -> None:
    """Swap in the tenant's own opening and closing lines (``main.py``).

    Mirrors ``configure_knowledge``: before this is called the tools are wired to neutral wording
    that greets and closes correctly but names nobody, which is the same degrade-don't-crash trade
    ``_NoFacts`` makes for a tenant with no facts.
    """
    register(Greet(opening))
    register(CloseConversation(closing))


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
