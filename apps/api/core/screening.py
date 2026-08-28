"""The clinical gate on booking (demo step 7).

The clinic vocabulary's ``booking_enquiry`` carries the rule — *never book a treatment when
screening or a clinician has blocked it* — and until this module there was nothing behind it. The
consequence is not subtle. The rest of the demo is a receptionist that can now actually write an
appointment, which means it can write one for a filler injection into a pregnant patient, on a
Tuesday afternoon, entirely by itself, and then confirm it with a reference number.

**Two halves, catching different things.**

*Some treatments are never the receptionist's to book.* Injectables are a medical procedure. No
disclosure is needed and no question is asked: a filler booking goes to a clinician because that is
what the clinic's licence says, not because the patient looked risky. This is
``screened_categories``, and it is a property of the catalogue.

*Some patients are never the receptionist's to book.* Pregnancy, isotretinoin, an anticoagulant —
the treatment is ordinary and the person is not, and weighing that is a clinical judgement. This is
``triggers``, and it is a property of what somebody said about themselves.

**A match ends autonomy and does nothing else.** It does not reassure, does not explain, does not
ask a follow-up question. Asking "how many months?" is a medical-history interview conducted in
order to decide, which is exactly what ``clinical_question``'s ``never`` list forbids the
receptionist from doing — and it is worse than useless, because it implies the answer would change
the outcome.

**Matching is on the personal-report form**, the same principle the emergency triggers are built
on and for the same reason: "أنا حامل" is a disclosure and "الليزر ينفع للحوامل؟" is a question
about the world. Only the first is about the person typing it, and only the first should stop a
booking. This module reuses ``core/emergency``'s matcher rather than growing a second one — the
Arabic folding tables and the Latin word-boundary rule exist once, and a disclosure written with a
hamza variant has to match the way a symptom report does.

**Absence is never a licence.** A vertical with no ``screening`` block gates nothing, which is
right for holiday homes and would be catastrophic to assume for a clinic — so the clinic
vocabulary declares one, and its contents are an unsigned clinical draft awaiting the medical
lead's approval. A term that is not in the list is a term nobody has written down, not a term
somebody has cleared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from packages.intents.schema import Vocabulary, default_vocabulary

from apps.api.core.emergency import normalise, phrase_matches

#: Why a booking stopped. ``disclosure`` is something the patient said; ``screened_treatment`` is
#: what they asked to book. Kept apart because they read differently in an audit: one is about a
#: person and the other is about the catalogue, and a clinic reviewing these wants to tell them
#: apart without reading the message.
ScreeningReason = Literal["disclosure", "screened_treatment"]


@dataclass(frozen=True, slots=True)
class ScreeningBlock:
    """The reason a booking may not be completed by the receptionist.

    Carries what matched rather than the message: the message is already on the ``messages`` row
    this will be filed against, and a disclosure copied into a second place is patient medical
    information stored somewhere nobody decided to store it.
    """

    reason: ScreeningReason
    action: str
    trigger_id: str | None = None
    matched: str | None = None
    category: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """The audit shape, deliberately the same flat dict an emergency detection produces."""
        return {
            "screening_block": True,
            "reason": self.reason,
            "trigger_id": self.trigger_id,
            "matched_phrase": self.matched,
            "category": self.category,
            "action": self.action,
        }


def screen(
    text: str | None,
    *,
    service_category: str | None = None,
    vocabulary: Vocabulary | None = None,
) -> ScreeningBlock | None:
    """What stops this booking, or ``None``.

    The disclosure check runs first. A patient who says they are pregnant while asking about a
    facial is blocked for the disclosure, not for the category, and the reason recorded should be
    the one a clinician would give.

    ``service_category`` is the catalogue category of the treatment being booked, once one has
    been resolved — ``None`` on the turns before that, which is why the disclosure half works from
    the first message and the category half only once there is a service to talk about.

    A vertical with no ``screening`` block returns ``None`` for everything. That is correct for a
    holiday-home guest and is why the clinic vocabulary declares one.
    """
    vocab = vocabulary or default_vocabulary()
    rules = vocab.screening
    if rules is None:
        return None

    if text and text.strip():
        normalised = normalise(text)
        for trigger in rules.triggers:
            for phrase in trigger.any_of:
                if phrase_matches(phrase, normalised):
                    return ScreeningBlock(
                        reason="disclosure",
                        action=rules.action,
                        trigger_id=trigger.id,
                        matched=phrase,
                    )

    if service_category is not None:
        folded = normalise(service_category)
        for category in rules.screened_categories:
            if normalise(category) == folded:
                return ScreeningBlock(
                    reason="screened_treatment",
                    action=rules.action,
                    category=service_category,
                )
    return None
