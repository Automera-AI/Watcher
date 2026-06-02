"""Tests for the rules engine (addendum §12)."""

from __future__ import annotations

from apps.api.rules.engine import RuleContext, evaluate
from apps.api.rules.models import (
    MessageContains,
    Rule,
    RuleAction,
    RuleCondition,
    SenderInList,
    SenderIsNew,
)


def _rule(
    rule_id: str, *, conditions: list[RuleCondition], priority: int = 0, enabled: bool = True
) -> Rule:
    return Rule(
        id=rule_id,
        name=rule_id,
        conditions=conditions,
        action=RuleAction(destination_id="dest-1"),
        priority=priority,
        enabled=enabled,
    )


def test_all_conditions_must_match() -> None:
    rule = _rule("r", conditions=[SenderIsNew(), MessageContains(text="quote")])
    ctx_match = RuleContext("+966500000000", "I need a QUOTE please", sender_is_new=True)
    ctx_no = RuleContext("+966500000000", "I need a quote", sender_is_new=False)
    assert evaluate([rule], ctx_match) is rule
    assert evaluate([rule], ctx_no) is None


def test_first_match_wins_by_priority() -> None:
    low = _rule("low", conditions=[SenderIsNew()], priority=10)
    high = _rule("high", conditions=[SenderIsNew()], priority=1)
    ctx = RuleContext("+966500000000", "hi", sender_is_new=True)
    assert evaluate([low, high], ctx) is high


def test_disabled_rule_is_skipped() -> None:
    rule = _rule("r", conditions=[SenderIsNew()], enabled=False)
    ctx = RuleContext("+966500000000", "hi", sender_is_new=True)
    assert evaluate([rule], ctx) is None


def test_sender_in_list_matches_e164() -> None:
    rule = _rule("vip", conditions=[SenderInList(values=["+966500000000"])])
    assert evaluate([rule], RuleContext("+966500000000", "hi", sender_is_new=False)) is rule
    assert evaluate([rule], RuleContext("+966511111111", "hi", sender_is_new=False)) is None


def test_no_rules_returns_none() -> None:
    assert evaluate([], RuleContext("+966500000000", "hi", sender_is_new=True)) is None
