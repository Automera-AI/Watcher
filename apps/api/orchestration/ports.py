"""Ports the orchestrator depends on (addendum §4, §9, §12).

The orchestrator decides *what* happens to a message; these seams supply the conversation it
belongs to and persist what was decided. Every one of them is a protocol so the decision itself
stays unit-testable against doubles, with no database in the room.

**What is no longer here, and why.** ``RulesProvider`` used to be one of these. Rules and
destinations were v1's answer to "what happens to a message" — match a rule, route to a Sheet — and
A5 replaces that answer with a receptionist that replies. The rule engine, the ``rules`` table and
the ``destinations`` table are all retained for the control page (track D), which is where a human
routes something deliberately; what was removed is the orchestrator's dependency on them, because a
message that is answered has nowhere to be filed *to*. ``RulesProvider`` now lives beside the engine
it feeds, in ``apps/api/rules/engine.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.api.conversations.task import Task
from apps.api.identity.models import CrmRecord
from apps.api.identity.resolver import IncomingContact
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import ConfidenceBand, InboxStatus
from apps.api.schemas.envelope import InboundTurn, OutboundAction

# (tenant_id, incoming contact) → candidate cached records to dedup against (§9, crm_cache).
CrmLookup = Callable[[str, IncomingContact], list[CrmRecord]]


@dataclass(frozen=True, slots=True)
class ConversationState:
    """The conversation this turn belongs to, and the job already in flight on it.

    This is what continuity *is*: without it the receptionist is handed ``task=None`` on every
    message and asks the same question forever, because nothing carries the answer from one turn
    to the next.

    ``replies_sent`` counts what we have already said in service of ``task``. The vocabulary
    declares ``defaults.max_clarifying_turns`` and nothing has ever read it; it is read here,
    because a receptionist that cannot make progress must eventually stop asking and fetch a
    person rather than loop at a guest.
    """

    conversation_id: str
    task: Task | None = None
    replies_sent: int = 0


class ConversationStore(Protocol):
    """Conversation + task continuity across turns (addendum §4 ``conversations``/``task_rows``).

    Split into two calls on purpose. ``begin`` runs before the receptionist and answers "what is
    already going on here"; ``record_reply`` runs after and persists both halves of the exchange.
    A single call could not do either job, because the receptionist has to run in between.
    """

    def begin(self, turn: InboundTurn) -> ConversationState:
        """Open (or find) the conversation, record the inbound turn, load any active task."""
        ...

    def record_reply(
        self,
        state: ConversationState,
        turn: InboundTurn,
        task: Task | None,
        action: OutboundAction,
    ) -> None:
        """Persist the updated task and the reply, keyed off the turn that prompted it.

        ``task`` is ``None`` when the reply belongs to no job — which is the emergency path and
        only the emergency path (roadmap G3). The transcript still needs both halves of the
        exchange; what it must not acquire is a task row with an invented intent on it, so the
        active task (if there is one) is left exactly as it was for whichever person picks the
        conversation up.
        """
        ...


@dataclass(frozen=True, slots=True)
class ClassificationDraft:
    """One row for ``classifications``: the model's answer plus the telemetry it was produced with.

    The telemetry is carried rather than derived. ``latency_ms`` is measured by the classifier
    service across the whole tiered policy and ``prompt_version`` is the version that service was
    built with — neither is knowable here, and inventing them to fill the columns would make the
    table worse than empty, since an eval keyed to a prompt version it cannot trust proves nothing.
    """

    tenant_id: str
    message_id: str
    result: ClassificationResult
    model_used: str
    prompt_version: str
    latency_ms: int


class ClassificationWriter(Protocol):
    """Persists the classification and returns its id, so the inbox item can point at it."""

    def record(self, draft: ClassificationDraft) -> str | None: ...


@dataclass(frozen=True, slots=True)
class InboxItemDraft:
    """What the orchestrator hands the inbox store to persist (addendum §4 ``inbox_items``).

    ``model_used`` used to be here and went nowhere — ``inbox_items`` has no such column, so the
    value was passed across a seam and dropped. It belongs on the classification row, which now
    exists; the inbox item carries ``classification_id`` and reaches the rest through it.
    """

    tenant_id: str
    message_id: str
    status: InboxStatus
    band: ConfidenceBand | None
    classification_id: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)


class InboxWriter(Protocol):
    """Persists an inbox item for the control page."""

    def create(self, draft: InboxItemDraft) -> None: ...
