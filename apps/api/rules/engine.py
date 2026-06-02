"""Rule evaluation (addendum §12).

Conditions are ANDed; rules are tried in ascending ``priority`` and the first enabled match wins. A
match auto-routes the message (the caller writes the audit entry with ``actor=bot`` and still
surfaces the item in the inbox marked ``auto``).
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.rules.models import (
    MessageContains,
    Rule,
    RuleCondition,
    SenderInList,
    SenderIsNew,
)


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
