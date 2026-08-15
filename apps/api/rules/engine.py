"""Rule evaluation (addendum §12).

Conditions are ANDed; rules are tried in ascending ``priority`` and the first enabled match wins.

**Who calls this, as of A5.** Not the orchestrator. Auto-routing a message to a destination was
v1's answer to an inbound message and the receptionist is v2's; an answered message has nowhere to
be filed to. The engine, the ``rules`` table and the stored-rule adapter are retained rather than
dropped, because deliberate routing by a human is a control-page feature (track D) and this is the
evaluator it will use. Retained, not wired: nothing in the message path reads a rule today.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from apps.api.rules.models import (
    MessageContains,
    Rule,
    RuleCondition,
    SenderInList,
    SenderIsNew,
)

#: tenant_id → that tenant's enabled rules (priority order handled by the engine). Lives here
#: rather than in the orchestrator's ports now that the orchestrator does not depend on it.
RulesProvider = Callable[[str], list[Rule]]


@dataclass(frozen=True, slots=True)
class RuleContext:
    """The facts a rule is evaluated against, for one message."""

    sender_phone_e164: str
    message_text: str
    sender_is_new: bool


def _matches(condition: RuleCondition, ctx: RuleContext) -> bool:
    match condition:
        case SenderInList():
            return ctx.sender_phone_e164 in condition.values
        case SenderIsNew():
            return ctx.sender_is_new
        case MessageContains():
            haystack = ctx.message_text
            needle = condition.text
            if condition.case_insensitive:
                haystack, needle = haystack.lower(), needle.lower()
            return needle in haystack


def evaluate(rules: list[Rule], ctx: RuleContext) -> Rule | None:
    """Return the first enabled rule (by priority) whose conditions all match, or ``None``."""
    for rule in sorted(rules, key=lambda r: r.priority):
        if not rule.enabled:
            continue
        if all(_matches(condition, ctx) for condition in rule.conditions):
            return rule
    return None
