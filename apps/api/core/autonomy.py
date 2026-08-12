"""How much the receptionist is allowed to do on its own.

The single most important function in the codebase: everything done without a human watching
passes through here. Ported from the v2 scaffold in roadmap 1.2.

**The ordering is the rule.** Authority is checked before confidence, because confidence is not
authority. A model that is 99% certain it understood a cancellation is still not allowed to
process the refund, and a door code still goes nowhere until the person asking has proved who
they are. Only once those are settled does the confidence band get a say.

**Change from the scaffold.** It hardcoded ``ALWAYS_HUMAN`` and ``REQUIRES_VERIFIED_IDENTITY``
as frozensets sitting beside a second copy of the intent list. Both now come from the
vocabulary — ``max_autonomy`` and ``needs_verified_identity`` per intent — so the ceiling is
declared once, in the file an operator reads. Two divergences were resolved in the vocabulary's
favour when they were merged:

* the scaffold let a *verified* guest cancel autonomously. The vocabulary says a cancellation is
  a refund and a refund always reaches a person, whoever is asking (README decision 2);
* the scaffold's always-human list omitted payment questions and complaints.

``max_autonomy`` is a **ceiling, not a decision**: the result is the lower of what the intent
permits and what the confidence band allows. Half confident is fine for filing a message; it is
not enough to hold a unit.
"""

from __future__ import annotations

from typing import Literal

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.schemas.common import HIGH_CONFIDENCE_THRESHOLD, MEDIUM_CONFIDENCE_THRESHOLD

Autonomy = Literal["act", "act_and_notify", "hand_off"]

#: Lowest wins. Used to take the lower of the intent's ceiling and the confidence band.
_RANK: dict[str, int] = {"hand_off": 0, "act_and_notify": 1, "act": 2}


def _band_allows(confidence: float) -> Autonomy:
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return "act"
    if confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "act_and_notify"
    return "hand_off"


def decide_autonomy(
    intent: str,
    confidence: float,
    *,
    identity_verified: bool = False,
    emergency: bool = False,
    vocabulary: Vocabulary | None = None,
) -> Autonomy:
    """What this turn is allowed to do by itself.

    ``emergency`` short-circuits everything. A gas leak filed as a maintenance ticket is worse
    than no receptionist at all, so the emergency triggers are checked before intent, before
    confidence, before this function has an opinion.
    """
    vocab = vocabulary or default_vocabulary()

    if emergency:
        return "hand_off"

    known = {i.name: i for i in vocab.intents}
    if (declared := known.get(intent)) is None:
        # An intent nobody declared is not a licence to improvise.
        return "hand_off"

    if declared.max_autonomy == "hand_off":
        return "hand_off"

    if declared.needs_verified_identity and not identity_verified:
        return "hand_off"

    ceiling: Autonomy = declared.max_autonomy
    allowed = _band_allows(confidence)
    return ceiling if _RANK[ceiling] < _RANK[allowed] else allowed
