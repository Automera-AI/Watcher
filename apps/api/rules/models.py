"""Rule schemas (addendum §12). Conditions are a simple ANDed list — no DSL."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class SenderInList(BaseModel):
    """Match when the sender's E.164 is in ``values``."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["sender_in_list"] = "sender_in_list"
    values: list[str]


class SenderIsNew(BaseModel):
    """Match when the sender has no prior record (per the identity check, §9)."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["sender_is_new"] = "sender_is_new"


class MessageContains(BaseModel):
    """Match when the message text contains ``text`` (case-insensitive by default)."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["message_contains"] = "message_contains"
    text: str
    case_insensitive: bool = True


# Discriminated union so conditions round-trip through the ``rules.conditions`` jsonb column.
RuleCondition = Annotated[
    SenderInList | SenderIsNew | MessageContains,
    Field(discriminator="type"),
]


class RuleAction(BaseModel):
    """What a matched rule does: auto-route to a destination (audit ``actor=bot``, §12)."""

    model_config = ConfigDict(extra="forbid")
    destination_id: str
    record_type: str | None = None


class Rule(BaseModel):
    """A single auto-routing rule (addendum §4 ``rules``)."""

    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    conditions: list[RuleCondition]
    action: RuleAction
    enabled: bool = True
    priority: int = 0  # lower runs first; first match wins
