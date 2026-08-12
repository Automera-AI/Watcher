"""The autonomy gate: what the receptionist may do alone, and what always fetches a person (1.2).

Two things from the scaffold had to survive porting, and both are here:

  * **Money and owner matters reach a person regardless of confidence**, checked *before* the
    confidence band. Confidence is not authority. A model that is 99% sure it understood a
    cancellation is still not allowed to process the refund.
  * **An access code requires proof of identity**, not a good identity *match*.

**The gap trap #3 found.** The scaffold assumed an ``identity_verified`` flag. The repo does
identity *matching* — "this is probably the same person as this record", a similarity score in
``identity/resolver.py`` — which is a different claim from "this person has proved who they are".
Matching is an inference the system makes about a sender. Verification is something the sender
does. Confusing them is how a door code goes to whoever is holding the phone, so the gate needs
the verification-codes table before ``access_code_request`` can be answered at all.

The vocabulary half is live and runs today. The gate itself is item 2.3 and the verification table
is part of 2.1, so those are `xfail(strict=True)` — when they land, XPASS forces the markers off.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from packages.intents import schema

VOCAB = schema.load(Path(__file__).resolve().parents[3] / "packages/intents/intents.yaml")

#: Money and owner matters. Authority, not confidence, decides these.
ALWAYS_A_PERSON = frozenset(
    {"cancel_reservation", "billing_question", "payment_question", "complaint"}
)


# ── live today: the vocabulary's contract with the gate ───────────────────────


def test_every_intent_gives_the_gate_a_ceiling_to_read() -> None:
    """The gate takes the lower of this and what confidence allows, so it must always exist."""
    for intent in VOCAB.intents:
        assert intent.max_autonomy in {"act", "act_and_notify", "hand_off"}


def test_money_and_owner_matters_are_capped_at_hand_off_in_the_data() -> None:
    """Before any gate logic runs, the vocabulary itself must already forbid acting alone.

    Belt and braces on purpose: this is the rule most likely to be relaxed under volume
    pressure, and the cheapest place to catch that is the file it would be relaxed in.
    """
    by_name = {i.name: i for i in VOCAB.intents}
    for name in ALWAYS_A_PERSON:
        assert by_name[name].max_autonomy == "hand_off", (
            f"{name} touches money or ownership and must reach a person whatever the confidence"
        )


def test_an_access_code_needs_proof_of_identity_and_a_booking_to_check_against() -> None:
    from packages.intents.schema import MUST_VERIFY

    access = next(i for i in VOCAB.intents if i.name == "access_code_request")
    assert access.name in MUST_VERIFY
    assert access.needs_verified_identity is True
    assert "reservation_ref" in access.required_slots, "nothing to check the claim against"


def test_the_repo_does_identity_matching_not_identity_verification() -> None:
    """Pins trap #3 so it is not rediscovered.

    ``resolver.decide`` returns MERGE / LINK_RELATED / NEW from a similarity score. Nowhere in
    that path does a sender *prove* anything. When the verification-codes table lands, this test
    should start failing and be replaced by the real one below it.
    """
    from apps.api.identity import resolver

    assert hasattr(resolver, "decide"), "identity matching should still exist"

    assert resolver.__file__ is not None
    source = Path(resolver.__file__).read_text(encoding="utf-8").lower()
    assert "identity_verified" not in source, (
        "if this now exists, verification has landed — retire this test and unmark the next one"
    )


# ── specification for items 2.1 and 2.3 ───────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="verification codes are part of roadmap item 2.1")
def test_a_matched_sender_is_still_not_a_verified_sender() -> None:
    """The distinction that keeps a door code from reaching whoever is holding the phone."""
    from apps.api.identity.verification import is_verified

    matched_but_unproven = {"identity_decision": "merge", "match_score": 0.99}
    assert is_verified(matched_but_unproven) is False


@pytest.mark.xfail(strict=True, reason="autonomy gate is roadmap item 2.3")
def test_the_gate_takes_the_lower_of_the_ceiling_and_the_confidence_band() -> None:
    """``max_autonomy`` is a ceiling, not a decision. Half confident is fine for filing a
    message; it is not enough to hold a unit."""
    from apps.api.core.autonomy import allowed_action

    assert allowed_action(intent="booking_enquiry", confidence=0.95) == "act_and_notify"
    assert allowed_action(intent="booking_enquiry", confidence=0.40) == "hand_off"


@pytest.mark.xfail(strict=True, reason="autonomy gate is roadmap item 2.3")
def test_authority_is_checked_before_confidence() -> None:
    """The ordering is the rule. A very confident cancellation is still a person's decision,
    so the money check has to run before the confidence band is even consulted."""
    from apps.api.core.autonomy import allowed_action

    for name in sorted(ALWAYS_A_PERSON):
        assert allowed_action(intent=name, confidence=0.99) == "hand_off", (
            f"{name} escaped to an autonomous action on high confidence — the money check is "
            "running after the band, or not at all"
        )


@pytest.mark.xfail(strict=True, reason="autonomy gate is roadmap item 2.3")
def test_an_access_code_is_refused_until_identity_is_proved() -> None:
    from apps.api.core.autonomy import allowed_action

    assert (
        allowed_action(intent="access_code_request", confidence=0.99, identity_verified=False)
        == "hand_off"
    )
    assert (
        allowed_action(intent="access_code_request", confidence=0.99, identity_verified=True)
        == "act"
    )


@pytest.mark.xfail(strict=True, reason="autonomy gate is roadmap item 2.3")
def test_an_emergency_outranks_the_gate_entirely() -> None:
    """Emergencies are checked before intent, before confidence, before anything. A gas leak
    filed as a maintenance ticket is worse than no receptionist at all."""
    from apps.api.core.autonomy import allowed_action

    assert allowed_action(intent="maintenance_issue", confidence=0.99, emergency=True) == "hand_off"
