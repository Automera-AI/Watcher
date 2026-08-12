"""The safety rules. If one of these ever fails, stop and fix it before shipping."""

import pytest

from app.core.understanding import PropertyIntent, Understanding, decide_autonomy


def make(intent, confidence=0.95):
    return Understanding(
        intent=intent,
        summary_one_line="test",
        confidence_overall=confidence,
        confidence_intent=confidence,
        confidence_slots=confidence,
    )


def test_confident_and_harmless_acts_alone():
    assert decide_autonomy(make(PropertyIntent.GENERAL_INFO), identity_verified=False) == "act"


def test_unsure_always_hands_off():
    u = make(PropertyIntent.GENERAL_INFO, confidence=0.4)
    assert decide_autonomy(u, identity_verified=False) == "hand_off"


@pytest.mark.parametrize(
    "intent", [PropertyIntent.BILLING_QUESTION, PropertyIntent.OWNER_ENQUIRY]
)
def test_money_and_owners_always_reach_a_human(intent):
    """Even at full confidence. Confidence is not the same as authority."""
    assert decide_autonomy(make(intent), identity_verified=True) == "hand_off"


def test_door_codes_need_proof_of_identity():
    u = make(PropertyIntent.ACCESS_CODE_REQUEST)
    assert decide_autonomy(u, identity_verified=False) == "hand_off"
    assert decide_autonomy(u, identity_verified=True) == "act"


def test_middling_confidence_acts_but_tells_someone():
    u = make(PropertyIntent.GENERAL_INFO, confidence=0.7)
    assert decide_autonomy(u, identity_verified=False) == "act_and_notify"
