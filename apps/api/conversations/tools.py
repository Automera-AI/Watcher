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


class _NoFacts:
    """The default knowledge lookup: no tenant has any facts. Every match is a real "I don't
    know" rather than a crash — the same trade ``channels/factory.py`` makes for a process with
    no send credentials: a degraded, working state, not a refusal to run."""

    def search(self, tenant_id: str) -> list[Fact]:
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
    """

    name = "answer_from_knowledge"
    description = "Look up an answer to a guest's question in the tenant's knowledge base."

    def __init__(self, knowledge: KnowledgeLookup) -> None:
        self._knowledge = knowledge

    async def run(self, **kwargs: Any) -> ToolResult:
        tenant_id: str = kwargs["tenant_id"]
        question: str = kwargs.get("question") or ""
        identity_verified: bool = bool(kwargs.get("identity_verified", False))

        facts = self._knowledge.search(tenant_id)
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


def configure_knowledge(knowledge: KnowledgeLookup) -> None:
    """Swap in a real knowledge lookup once the composition root has one (``main.py``).

    Mirrors how ``register`` already works: the registry is a small, named, module-level service
    locator (A5), not a fresh idea introduced here. Before this is called, ``answer_from_knowledge``
    is wired to ``_NoFacts`` and behaves exactly like a tenant with an empty knowledge base.
    """
    register(AnswerFromKnowledge(knowledge))


def validate_registry(vocabulary: Vocabulary | None = None) -> list[str]:
    """Return tool names in the registry that the vocabulary does not declare."""
    vocab = vocabulary or default_vocabulary()
    declared = {i.terminal_tool for i in vocab.intents if i.terminal_tool}
    return [name for name in REGISTRY if declared and name not in declared]
