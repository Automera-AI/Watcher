"""The safety rules. If one of these ever fails, stop and fix it before shipping.

Ported from the v2 scaffold in roadmap 1.2. All five scaffold tests are here. Two of them assert
something stricter than the scaffold did, because the vocabulary and the scaffold disagreed and
the vocabulary is the later, reasoned decision:

* the scaffold let a **verified** guest cancel autonomously — ``cancel_reservation`` sat in its
  ``REQUIRES_VERIFIED_IDENTITY`` set, not its always-human one. A cancellation is a refund and a
  refund is money going backwards, so it reaches a person whoever is asking;
* the scaffold's always-human list was ``{billing_question, owner_enquiry}``. Payment questions
  and complaints were missing.

**Trap #3 is still open and this file is honest about it.** ``identity_verified`` is a parameter
the caller supplies; nothing in this repo can currently produce it. The repo does identity
*matching* — a similarity score in ``identity/resolver.py`` saying "probably the same person as
this record" — which is not the same claim as "this person has proved who they are". Matching is
an inference the system makes; verification is something the sender does. Confusing the two is
how a door code reaches whoever is holding the phone. The verification-codes table is item 2.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.api.core.autonomy import decide_autonomy

# ── ported from the scaffold ──────────────────────────────────────────────────


def test_confident_and_harmless_acts_alone() -> None:
    assert decide_autonomy("general_info", 0.95, identity_verified=False) == "act"


def test_unsure_always_hands_off() -> None:
    assert decide_autonomy("general_info", 0.4, identity_verified=False) == "hand_off"


def test_middling_confidence_acts_but_tells_someone() -> None:
    assert decide_autonomy("general_info", 0.7, identity_verified=False) == "act_and_notify"


@pytest.mark.parametrize(
    "intent",
    ["billing_question", "owner_enquiry", "payment_question", "complaint", "cancel_reservation"],
)
def test_money_and_owners_always_reach_a_human(intent: str) -> None:
    """Even at full confidence, and even verified. Confidence is not authority.

    The last two entries are stricter than the scaffold: it allowed a verified guest to cancel,
    and it did not list payment questions or complaints at all.
    """
    assert decide_autonomy(intent, 0.99, identity_verified=True) == "hand_off"


def test_door_codes_need_proof_of_identity() -> None:
    assert decide_autonomy("access_code_request", 0.95, identity_verified=False) == "hand_off"
    assert decide_autonomy("access_code_request", 0.95, identity_verified=True) == "act"


# ── added: the ordering, the ceiling, and the emergency override ──────────────


def test_authority_is_checked_before_confidence() -> None:
    """The ordering is the rule, not an implementation detail. If the confidence band were
    consulted first, a high-confidence cancellation would escape before the money check ran."""
    for intent in ("billing_question", "cancel_reservation", "owner_enquiry"):
        assert decide_autonomy(intent, 1.0, identity_verified=True) == "hand_off"


def test_max_autonomy_is_a_ceiling_not_a_decision() -> None:
    """Half confident is fine for filing a message. It is not enough to hold a unit."""
    assert decide_autonomy("booking_enquiry", 0.99) == "act_and_notify"  # ceiling caps it
    assert decide_autonomy("booking_enquiry", 0.7) == "act_and_notify"  # band agrees
    assert decide_autonomy("booking_enquiry", 0.4) == "hand_off"  # band caps it


def test_an_emergency_outranks_everything() -> None:
    """Emergencies are checked before intent, before confidence, before this function has an
    opinion. A gas leak filed as a maintenance ticket is worse than no receptionist."""
    assert decide_autonomy("maintenance_issue", 0.99, emergency=True) == "hand_off"
    assert decide_autonomy("general_info", 0.99, emergency=True) == "hand_off"


def test_an_undeclared_intent_is_not_a_licence_to_improvise() -> None:
    assert decide_autonomy("arrange_airport_pickup", 0.99, identity_verified=True) == "hand_off"


# ── trap #3: the thing that does not exist yet ────────────────────────────────


def test_the_repo_still_cannot_prove_who_a_sender_is() -> None:
    """Pins trap #3 so it is not rediscovered, and fails the day it stops being true.

    When verification lands, this test should fail — retire it then, and make
    ``identity_verified`` something the caller reads rather than asserts.
    """
    from apps.api.identity import resolver

    assert hasattr(resolver, "decide"), "identity matching should still exist"
    assert resolver.__file__ is not None
    source = Path(resolver.__file__).read_text(encoding="utf-8").lower()
    assert (
        "identity_verified" not in source
    ), "verification appears to have landed — retire this test and wire the gate to it"
