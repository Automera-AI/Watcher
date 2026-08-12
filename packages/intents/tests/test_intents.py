"""Tests for the intent vocabulary.

Two kinds. The first says the file as written is valid. The second says the safety rules
cannot be quietly removed, by breaking them on purpose and checking the loader refuses.

The second kind is the one that earns its keep. A year from now someone will be in a hurry,
will want `cancel_reservation` to just handle it, and will change one word in a YAML file.
These tests are what stops that reaching a guest.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from packages.intents import schema

HERE = Path(__file__).resolve().parent.parent
REPO_ROOT = HERE.parents[1]
BASE = HERE / "intents.yaml"


@pytest.fixture
def raw() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(BASE.read_text(encoding="utf-8"))
    return loaded


def build(raw: dict[str, Any]) -> schema.Vocabulary:
    return schema.Vocabulary.model_validate(raw)


def find(raw: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = next(i for i in raw["intents"] if i["name"] == name)
    return found


# ── the file as it stands ─────────────────────────────────────────────────────


def test_the_file_is_valid() -> None:
    vocab = schema.load(BASE)
    assert vocab.vertical == "holiday_homes"
    assert set(vocab.markets) == {"AE", "EG"}


def test_every_client_example_is_valid() -> None:
    vocab = schema.load(BASE)
    clients = sorted((HERE / "clients").glob("*.yaml"))
    assert clients, "no client examples to check"
    for path in clients:
        client = schema.load_client(path)
        client.check_against(vocab)


def test_franco_arabic_is_a_declared_language() -> None:
    """Egyptian Arabic typed in Latin letters. If this is not its own tag, the per-language
    accuracy measure hides it inside Egyptian Arabic and you never see it failing."""
    vocab = schema.load(BASE)
    assert "ar-EG-latin" in {lang.code for lang in vocab.languages}
    used = {e.lang for i in vocab.intents for e in i.examples}
    assert "ar-EG-latin" in used, "declared but never exercised"


def test_franco_arabic_is_never_offered_to_a_speech_model() -> None:
    """Nobody says "3ayez ahgez" out loud. A phone test set filters on `spoken`, so the flag
    has to be set here or item 3.2 will happily build a call test set out of typed-only text."""
    vocab = schema.load(BASE)
    assert "ar-EG-latin" not in vocab.spoken_languages
    assert vocab.spoken_languages == {"en", "ar-AE", "ar-EG", "mixed"}


def test_an_acting_intent_cannot_be_testable_only_in_text(raw: dict[str, Any]) -> None:
    """Strip an acting intent down to Franco-Arabic and its phone test set is empty."""
    find(raw, "availability_check")["examples"] = [
        {"lang": "ar-EG-latin", "text": "fe sha22a fadya men 12 le 15?"}
    ]
    with pytest.raises(ValidationError, match="nothing to test on a phone call"):
        build(raw)


def test_every_acting_intent_has_examples_in_more_than_one_language() -> None:
    vocab = schema.load(BASE)
    for intent in vocab.intents:
        if intent.max_autonomy == "hand_off":
            continue
        langs = {e.lang for e in intent.examples}
        assert len(langs) >= 2, f"{intent.name} acts alone but is only tested in {langs}"


def test_emergencies_are_checked_before_maintenance() -> None:
    """A gas leak must not be filed as a broken appliance."""
    vocab = schema.load(BASE)
    triggers = {t.id for t in vocab.emergency.triggers}
    assert {"gas", "fire", "flood", "medical"} <= triggers
    assert vocab.emergency.action == "handoff_to_human"
    assert vocab.emergency.reply_immediately is True


def test_no_emergency_trigger_contains_a_lookalike_character() -> None:
    """Regression. The fire trigger shipped as "7arі2" with a Cyrillic і (U+0456) standing in
    for the Latin i — identical on screen, and it would never have matched a real message.

    A trigger that silently cannot fire is the worst defect this file can carry, and reading it
    does not find it. Every phrase is Latin or Arabic; never both.
    """
    vocab = schema.load(BASE)
    for trigger in vocab.emergency.triggers:
        for phrase in trigger.any_of:
            scripts = schema._script_of(phrase)
            assert len(scripts) <= 1, f"{trigger.id}: {phrase!r} mixes {sorted(scripts)}"
    fire = next(t for t in vocab.emergency.triggers if t.id == "fire")
    assert "7ari2" in fire.any_of
    assert all(ch.isascii() for ch in "7ari2")


def test_a_lookalike_character_fails_the_build(raw: dict[str, Any]) -> None:
    fire = next(t for t in raw["emergency"]["triggers"] if t["id"] == "fire")
    fire["any_of"] = ["7arі2"]  # Cyrillic і, the bug that shipped
    with pytest.raises(ValidationError, match="lookalike character"):
        build(raw)


# ── the safety rules cannot be removed ────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(schema.MUST_HAND_OFF))
def test_cannot_let_a_hand_off_intent_act(raw: dict[str, Any], name: str) -> None:
    find(raw, name)["max_autonomy"] = "act"
    with pytest.raises(ValidationError, match="must be hand_off"):
        build(raw)


@pytest.mark.parametrize("name", sorted(schema.MUST_VERIFY))
def test_cannot_drop_the_identity_check(raw: dict[str, Any], name: str) -> None:
    find(raw, name)["needs_verified_identity"] = False
    with pytest.raises(ValidationError, match="proof of identity"):
        build(raw)


def test_cannot_ask_for_proof_without_something_to_check_against(raw: dict[str, Any]) -> None:
    intent = find(raw, "access_code_request")
    intent["required_slots"] = []
    intent["confirm_before_acting"] = []
    with pytest.raises(ValidationError, match="nothing to check them against"):
        build(raw)


def test_cannot_remove_the_no_discount_rule(raw: dict[str, Any]) -> None:
    find(raw, "price_enquiry")["never"] = ["quote a rate from memory"]
    with pytest.raises(ValidationError, match="no rule against discounting"):
        build(raw)


def test_cannot_point_an_intent_at_a_tool_that_does_not_exist(raw: dict[str, Any]) -> None:
    find(raw, "availability_check")["terminal_tool"] = "check_availabilty"  # typo on purpose
    with pytest.raises(ValidationError, match="unknown tool"):
        build(raw)


def test_cannot_quote_from_memory(raw: dict[str, Any]) -> None:
    raw["quoting"]["never_from_memory"] = False
    with pytest.raises(ValidationError):
        build(raw)


def test_cannot_serve_a_stale_price(raw: dict[str, Any]) -> None:
    raw["quoting"]["max_age_seconds"] = 86400
    with pytest.raises(ValidationError):
        build(raw)


def test_cannot_delete_a_safety_critical_intent(raw: dict[str, Any]) -> None:
    raw["intents"] = [i for i in raw["intents"] if i["name"] != "complaint"]
    with pytest.raises(ValidationError, match="missing from the file"):
        build(raw)


def test_cannot_use_an_undeclared_language_in_an_example(raw: dict[str, Any]) -> None:
    find(raw, "availability_check")["examples"].append({"lang": "fr", "text": "bonjour"})
    with pytest.raises(ValidationError, match="undeclared language"):
        build(raw)


# ── clients can narrow the rules, never widen them ────────────────────────────


def test_a_client_cannot_switch_off_a_safety_critical_intent() -> None:
    vocab = schema.load(BASE)
    client = schema.load_client(HERE / "clients" / "egypt-holiday-homes.yaml")
    client.disabled_intents = ["cancel_reservation"]
    with pytest.raises(ValueError, match="cannot disable safety-critical"):
        client.check_against(vocab)


def test_a_client_cannot_invent_an_intent() -> None:
    vocab = schema.load(BASE)
    client = schema.load_client(HERE / "clients" / "dubai-holiday-homes.yaml")
    client.force_hand_off = ["arrange_airport_pickup"]
    with pytest.raises(ValueError, match="unknown intents"):
        client.check_against(vocab)


def test_a_client_may_force_more_things_to_a_human() -> None:
    """Narrowing is always allowed. The Egyptian example does exactly this for prices,
    because rates there are seasonal and negotiated by a person."""
    vocab = schema.load(BASE)
    client = schema.load_client(HERE / "clients" / "egypt-holiday-homes.yaml")
    assert "price_enquiry" in client.force_hand_off
    client.check_against(vocab)


# ── a price must be checkable, or it is not said ──────────────────────────────


def test_a_client_on_a_spreadsheet_cannot_quote_prices() -> None:
    """The one combination that puts an unverifiable number in front of a guest.

    No API means no way to ask, and no rate identifier means no way to prove afterwards what
    the number was when it was said. So it fails the build, not the code review.
    """
    vocab = schema.load(BASE)
    client = schema.load_client(HERE / "clients" / "egypt-holiday-homes.yaml")
    client.quote_prices = True
    client.force_hand_off = []
    with pytest.raises(ValueError, match="cannot be re-checked"):
        client.check_against(vocab)


def test_a_client_with_no_connected_system_cannot_quote_prices() -> None:
    """The state every client is in before the read path in roadmap item 3.1 lands."""
    vocab = schema.load(BASE)
    client = schema.load_client(HERE / "clients" / "dubai-holiday-homes.yaml")
    client.property_system = "none"
    with pytest.raises(ValueError, match="cannot be re-checked"):
        client.check_against(vocab)


def test_a_client_cannot_name_a_property_system_that_does_not_exist() -> None:
    vocab = schema.load(BASE)
    client = schema.load_client(HERE / "clients" / "dubai-holiday-homes.yaml")
    client.property_system = "hostway"  # typo on purpose
    with pytest.raises(ValueError, match="unknown property system"):
        client.check_against(vocab)


def test_quoting_off_and_price_enquiry_still_live_is_caught() -> None:
    """Otherwise the receptionist owns an intent it has no way to fulfil."""
    vocab = schema.load(BASE)
    client = schema.load_client(HERE / "clients" / "egypt-holiday-homes.yaml")
    client.force_hand_off = []
    with pytest.raises(ValueError, match="must be in"):
        client.check_against(vocab)


def test_a_quote_must_carry_enough_to_reproduce_it(raw: dict[str, Any]) -> None:
    raw["quoting"]["provenance_required"] = ["fetched_at"]
    with pytest.raises(ValidationError, match="cannot be re-checked"):
        build(raw)


def test_cannot_turn_off_the_quotable_system_requirement(raw: dict[str, Any]) -> None:
    raw["quoting"]["requires_quotable_system"] = False
    with pytest.raises(ValidationError):
        build(raw)


def test_cannot_repeat_a_price_from_earlier_in_the_conversation() -> None:
    """A price said ten minutes ago is a price from memory, which is the thing being banned."""
    vocab = schema.load(BASE)
    joined = " ".join(vocab.quoting.never)
    assert "repeat a price" in joined
    assert "did not answer" in joined


# ── the compiled output is the same thing, only faster ────────────────────────


def test_compiled_json_matches_the_yaml() -> None:
    subprocess.run(
        [sys.executable, "-m", "packages.intents.compile"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    from_yaml = schema.load(BASE)
    from_json = schema.load_compiled(HERE / "build" / "intents.json")
    assert from_json.model_dump() == from_yaml.model_dump()
