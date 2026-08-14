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


register(TakeMessage())
register(HandoffToHuman())


def validate_registry(vocabulary: Vocabulary | None = None) -> list[str]:
    """Return tool names in the registry that the vocabulary does not declare."""
    vocab = vocabulary or default_vocabulary()
    declared = {i.terminal_tool for i in vocab.intents if i.terminal_tool}
    return [name for name in REGISTRY if declared and name not in declared]
