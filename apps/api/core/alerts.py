"""Telling a person, right now (roadmap G3, the second half).

Detection without an alert is a log line nobody reads. ``intents.yaml`` declares
``alert: phone_call_to_operator`` beside the triggers, and this is the seam that carries it: the
core builds an :class:`EmergencyAlert` describing what fired and to whom it happened, and an
adapter delivers it on whatever channel that adapter actually has.

**Why the outcome says which channel it used.** The vocabulary asks for a phone call, and no
channel wired into this process can place one — the only one connected is a chat channel, and the
voice channel is a later item. Two dishonest options were available: treat a text notification as
satisfying ``phone_call_to_operator``, or refuse to alert at all because the declared channel is
unavailable. Both are worse than the third: deliver on the best channel there is, and report
exactly what was done, so ``AlertOutcome.channel`` beside ``EmergencyAlert.requested_channel``
is a one-line answer to "was the operator actually *called*?". Today it is always "no, they were
messaged", and that gap is a named roadmap item rather than a comfortable silence.

The protocol is asynchronous because delivery reaches a network, and returns rather than raises
because the caller — the orchestrator, mid-emergency — has a guest waiting and must not lose the
reply to a failed alert. An alerter that cannot deliver says so; it does not take the message path
down with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

#: What ``AlertOutcome.channel`` says when nothing was configured to deliver on. The orchestrator
#: still writes a CRITICAL log line in that case — see ``Orchestrator._raise_the_alarm``.
LOG_ONLY = "log"


@dataclass(frozen=True, slots=True)
class EmergencyAlert:
    """One operator alert, in channel-neutral terms.

    Everything an operator needs to act without opening the control page: who, where, what fired,
    and what they said. ``text`` is the guest's own words — an alert that says "possible gas leak"
    and withholds the sentence is an alert the operator has to go and look up.
    """

    tenant_id: str
    message_id: str
    trigger_id: str
    matched: str
    guest_identity: str
    """The channel identity to call back on — a phone number on every channel that has one."""

    thread_id: str
    text: str | None
    received_at: datetime
    requested_channel: str
    """What ``intents.yaml`` asked for (``emergency.alert``), not what was used."""

    def summary(self) -> str:
        """The alert as one plain-text block, for any channel that can carry text.

        Composed here rather than in each adapter so two channels cannot describe the same
        emergency differently. Ordered by what an operator does with it: what happened, who to
        reach, then the evidence.
        """
        said = (self.text or "").strip() or "(no text — check the message)"
        return (
            f"EMERGENCY — {self.trigger_id}\n"
            f"Guest: {self.guest_identity}\n"
            f"Received: {self.received_at.isoformat()}\n"
            f'Matched: "{self.matched}"\n'
            f"Said: {said}\n"
            f"Message id: {self.message_id}\n"
            "The guest has been told someone is contacting them immediately."
        )


@dataclass(frozen=True, slots=True)
class AlertOutcome:
    """Whether a person was reached, and by what means."""

    delivered: bool
    channel: str
    detail: str | None = None

    def satisfies(self, requested_channel: str) -> bool:
        """Whether delivery happened on the channel the vocabulary declared.

        ``False`` is the expected answer today and is not a failure — it is the difference between
        a text notification and the phone call ``intents.yaml`` asks for, kept visible.
        """
        return self.delivered and self.channel == requested_channel


class OperatorAlerter(Protocol):
    """Reaches a human about an emergency. One implementation per channel that can carry one."""

    async def alert(self, alert: EmergencyAlert) -> AlertOutcome: ...
