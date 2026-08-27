"""The emergency detector (roadmap G3).

Two kinds of test here and the distinction matters more than usual.

*Every declared trigger fires.* Parametrised over ``intents.yaml`` itself rather than over a copy,
so adding a trigger to the vocabulary without it being matchable fails here — the file is data an
operator edits, and data that is quietly unreachable is the failure mode this whole item exists to
remove.

*The near misses do not fire.* "Is there a fireplace?" and "can I smoke on the balcony?" are the
messages a substring matcher answers with a phone call at 3am. They are asserted individually,
because each one is a specific decision in ``core/emergency.py`` about how the two scripts match.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from packages.intents.schema import default_vocabulary, shipped_vocabularies

from apps.api.core.emergency import (
    DEFAULT_TIMEZONE,
    EMERGENCY_REPLY,
    EmergencyDetection,
    detect,
    emergency_reply,
    normalise,
    timezone_is_known,
)

VOCAB = default_vocabulary()

#: Mid-afternoon in Dubai, so the one time-windowed trigger is *not* armed unless a test says so.
AFTERNOON = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)  # 14:00 Asia/Dubai
NIGHT = datetime(2026, 8, 16, 22, 30, tzinfo=UTC)  # 02:30 Asia/Dubai, next day


def _detect(
    text: str | None, at: datetime = AFTERNOON, timezone: str = DEFAULT_TIMEZONE
) -> EmergencyDetection | None:
    return detect(text, at=at, timezone=timezone, vocabulary=VOCAB)


def _phrases() -> list[tuple[str, str]]:
    """(trigger id, phrase) for every phrase the vocabulary declares."""
    return [(t.id, phrase) for t in VOCAB.emergency.triggers for phrase in t.any_of]


@pytest.mark.parametrize(("trigger_id", "phrase"), _phrases(), ids=lambda v: v)
def test_every_declared_phrase_fires_its_trigger(trigger_id: str, phrase: str) -> None:
    """The whole file is reachable. A trigger nobody can reach is worse than no trigger."""
    # The night-only trigger is checked at night; everything else is time-independent.
    at = NIGHT if trigger_id == "locked_out_at_night" else AFTERNOON
    detection = detect(phrase, at=at, vocabulary=VOCAB)
    assert detection is not None, f"{phrase!r} matched nothing"
    assert detection.trigger_id == trigger_id
    assert detection.matched == phrase


def test_the_phrase_is_found_inside_a_sentence() -> None:
    """Nobody types the trigger and only the trigger."""
    detection = _detect("hi, sorry to bother you but there is a smell of gas in the kitchen")
    assert detection is not None
    assert detection.trigger_id == "gas"


def test_a_trigger_carries_the_vocabularys_action_and_alert() -> None:
    """The detector reads the file; nothing downstream re-reads it and drifts."""
    detection = _detect("there is a fire")
    assert detection is not None
    assert detection.action == VOCAB.emergency.action == "handoff_to_human"
    assert detection.alert == VOCAB.emergency.alert == "phone_call_to_operator"
    assert detection.snapshot()["emergency"] is True
    assert detection.snapshot()["trigger_id"] == "fire"


# ── The two scripts ────────────────────────────────────────────────────────────────────────


def test_arabic_matches_through_an_attached_article() -> None:
    """الحريق is حريق with the article on the front. A boundary test here invents a miss."""
    detection = _detect("في الحريق في المبنى")
    assert detection is not None
    assert detection.trigger_id == "fire"


def test_arabic_matches_without_the_ta_marbuta() -> None:
    """A guest typing ريحه rather than ريحة is a guest with a gas leak."""
    detection = _detect("في ريحه غاز في الشقة")
    assert detection is not None
    assert detection.trigger_id == "gas"


def test_arabic_diacritics_do_not_hide_a_trigger() -> None:
    detection = _detect("حَرِيق")
    assert detection is not None
    assert detection.trigger_id == "fire"


def test_franco_arabic_matches() -> None:
    """The language a phone test set cannot use and a large share of Egyptian WhatsApp is in."""
    detection = _detect("ya basha fi re7et ghaz gowa el sha2a")
    assert detection is not None
    assert detection.trigger_id == "gas"


def test_case_and_extra_whitespace_do_not_hide_a_trigger() -> None:
    detection = _detect("GAS   LEAK!!")
    assert detection is not None
    assert detection.trigger_id == "gas"


# ── The near misses ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Is there a fireplace in the living room?",
        "Are there fireworks for new year?",
        "Can we have a campfire on the beach?",
    ],
)
def test_fire_does_not_match_inside_another_word(text: str) -> None:
    """The reason Latin phrases match on word boundaries. None of these is an emergency."""
    assert _detect(text) is None


def test_smoking_is_not_smoke() -> None:
    assert _detect("Is smoking allowed on the balcony?") is None


def test_a_franco_trigger_does_not_match_inside_a_longer_token() -> None:
    """Digits are word characters here, so 7ari2 is a word and not a fragment."""
    assert _detect("my booking ref is x7ari2y") is None


def test_a_phrasing_the_vocabulary_does_not_declare_does_not_fire() -> None:
    """The limit of this design, asserted rather than left to be discovered.

    The gas trigger declares "smell of gas" and "gas leak". *"I smell gas"* — an obvious way to
    say it — matches neither, and this detector will not paraphrase: it matches phrases an
    operator wrote down, which is the property that makes ``intents.yaml`` reviewable by the
    person who carries the consequences.

    Widening the list is a one-line edit to that file and it is deliberately *their* edit, not
    this module's. It does not touch the classifier prompt and does not invalidate the recorded
    eval baseline. This test exists so the gap is visible in the suite rather than in an incident.
    """
    assert _detect("I smell gas") is None


def test_an_ordinary_message_is_not_an_emergency() -> None:
    assert _detect("Hi, is the apartment available on 4 June for two people?") is None


@pytest.mark.parametrize("text", [None, "", "   "])
def test_no_text_is_not_an_emergency(text: str | None) -> None:
    """An image nobody could read says nothing. It says so rather than guessing."""
    assert _detect(text) is None


# ── The one trigger with a clock ───────────────────────────────────────────────────────────


def test_locked_out_in_the_afternoon_is_a_support_request() -> None:
    """The vocabulary's own note, asserted: locked out at 2pm is not an emergency."""
    assert _detect("I'm locked out", at=AFTERNOON) is None


def test_locked_out_at_night_is_a_person_on_a_street() -> None:
    detection = _detect("I'm locked out", at=NIGHT)
    assert detection is not None
    assert detection.trigger_id == "locked_out_at_night"


def test_the_window_is_read_in_the_tenants_local_time() -> None:
    """The same instant is night in one market and evening in the other.

    19:30 UTC in January is 23:30 in Dubai — inside the 22:00–07:00 window — and 21:30 in Cairo,
    half an hour outside it. A detector reading the *server's* clock gets one of these wrong, and
    which one depends on where the container happens to run.

    January rather than August on purpose: Egypt observes summer time and the UAE does not, so a
    summer date would make this assertion a statement about the tzdata release as much as about
    the code.
    """
    evening = datetime(2026, 1, 16, 19, 30, tzinfo=UTC)
    assert _detect("locked out", at=evening, timezone="Asia/Dubai") is not None
    assert _detect("locked out", at=evening, timezone="Africa/Cairo") is None


def test_a_naive_timestamp_is_read_as_utc() -> None:
    """Every adapter in this tree produces UTC; a missing tzinfo must not move the window."""
    naive = datetime(2026, 8, 16, 22, 30)
    assert _detect("locked out", at=naive) is not None


# ── The plumbing the configuration layer leans on ──────────────────────────────────────────


def test_known_and_unknown_timezones() -> None:
    assert timezone_is_known(DEFAULT_TIMEZONE)
    assert timezone_is_known("Africa/Cairo")
    assert not timezone_is_known("Asia/Dubay")


def test_normalise_is_idempotent() -> None:
    for text in ("Smell of GAS", "ريحة غاز", "re7et  ghaz"):
        assert normalise(normalise(text)) == normalise(text)


def test_the_reply_speaks_both_languages() -> None:
    """A safety line is the last place to guess which language the guest reads."""
    assert "emergency" in EMERGENCY_REPLY.lower()
    assert "طارئة" in EMERGENCY_REPLY


# ── Clinic emergencies: recall against how a frightened person actually types ────────────────
#
# The trigger tests above check that every *declared* phrase fires. That is necessary and not
# sufficient: a vocabulary can declare only long, fully-formed sentences, pass that check
# completely, and still miss the real message. It happened — an earlier clinic draft replaced
# short phrases with compound ones like "جلدي اتحرق جامد بعد الليزر" and recall against realistic
# messages fell to 4/15 while every declared trigger still matched.
#
# So this suite tests the opposite direction: messages nobody wrote into the YAML. For emergencies
# the asymmetry is the whole point — a false positive costs a phone call, a false negative costs
# the thing the detector exists to prevent — so recall is asserted at 100% while the decoys below
# guard the other side.

_CLINICS = shipped_vocabularies()["clinics"]

#: Real-shaped emergency reports. Short, unpunctuated, mid-panic — not the tidy sentences a
#: vocabulary author writes. Arabic, Franco-Arabic and English, since all three reach this number.
_CLINIC_EMERGENCIES = [
    "مش قادرة اتنفس",
    "مش قادرة أتنفس ساعدوني",
    "وشي وارم اوي ومش قادرة اتنفس",
    "حلقي بيقفل",
    "لساني وارم",
    "حساسية شديدة",
    "فقدت النظر بعد الفيلر",
    "مبقتش شايفة بعد الحقن",
    "جلدي اتحرق من الليزر",
    "نزيف مش بيقف",
    "اغمي عليها ومش بتفوق",
    "I can't breathe",
    "she is unconscious",
    "my throat is closing up",
    "severe burn after laser",
]

#: The other side of the trade. Each is a phrase a *loose* matcher fires on, and each is an
#: ordinary question a clinic receives daily. "في حروق من الليزر عادة؟" is the one that caught an
#: earlier draft: it asks whether burns are normal, and a bare "حروق" trigger phoned a clinician.
#: The last two are the reason the vision trigger is qualified by the eye or by the injection
#: rather than matching "مش شايفة" — someone who cannot see a *message* is not losing their sight.
_CLINIC_NEAR_MISSES = [
    "الليزر بيحرق الجلد؟",
    "في حروق من الليزر عادة؟",
    "does laser burn the skin?",
    "بشرتي حساسة شوية",
    "الفيلر بيسبب حساسية؟",
    "ممكن الليزر يعمل فقاقيع؟",
    "عايزة احجز ليزر",
    "بكام جلسة الفيلر؟",
    "مش شايفة الرسالة",
    "مبقتش شايفة الرسايل",
]


@pytest.mark.parametrize("text", _CLINIC_EMERGENCIES)
def test_a_real_clinic_emergency_is_detected(text: str) -> None:
    """Recall, message by message, so a failure names the one that would have been missed."""
    assert detect(text, vocabulary=_CLINICS, at=datetime.now(UTC)) is not None


@pytest.mark.parametrize("text", _CLINIC_NEAR_MISSES)
def test_an_ordinary_clinic_question_does_not_page_a_clinician(text: str) -> None:
    """A clinician woken for a price question stops trusting the alert, and misses a real one."""
    assert detect(text, vocabulary=_CLINICS, at=datetime.now(UTC)) is None


# ── The tenant's own urgent contact ──────────────────────────────────────────────────────────


def test_without_a_configured_contact_the_reply_is_unchanged() -> None:
    """A tenant that has not given a number still gets the neutral, correct safety line."""
    assert emergency_reply(None) == EMERGENCY_REPLY
    assert emergency_reply("") == EMERGENCY_REPLY


def test_a_configured_contact_reaches_the_customer_in_both_languages() -> None:
    """The point of configuring it: the number is in the first message, not the call back."""
    reply = emergency_reply("+20 100 000 0000")
    assert reply.count("+20 100 000 0000") == 2, "one for each language"


def test_the_clinic_contact_never_replaces_emergency_services() -> None:
    """The one way this could do harm.

    Someone who cannot breathe needs an ambulance, not a clinic mobile. The tenant's number is an
    addition to the public-emergency line and must never displace it, so the original text has to
    survive intact underneath whatever is appended.
    """
    reply = emergency_reply("+20 100 000 0000")
    assert EMERGENCY_REPLY in reply
    assert "local emergency number" in reply
    assert "رقم الطوارئ المحلي" in reply
