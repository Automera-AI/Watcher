"""Tests for slot extraction and normalisation (demo step 5).

Every date here is written against a fixed ``today`` — Monday 31 August 2026, the day before the
demo — because the whole point of this module is that "بكرة" resolves in code that can be tested
rather than by a model reasoning about a calendar it cannot see.
"""

from __future__ import annotations

from datetime import date

import pytest
from packages.intents.schema import shipped_vocabularies, vocabulary_for

from apps.api.conversations.slots import (
    DATE_SLOTS,
    FREE_TEXT_DATE_SLOTS,
    TIME_SLOTS,
    declared_slots,
    normalise_slots,
    parse_date,
    parse_time,
    strip_unsupported_temporal_slots,
)

#: A Monday. The demo week runs 31 Aug – 6 Sep with Friday 4 Sep absent from the diary.
TODAY = date(2026, 8, 31)

CLINICS = vocabulary_for("clinics")
HOLIDAY_HOMES = vocabulary_for("holiday_homes")


# ── dates ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("2026-09-02", "2026-09-02"),
        ("النهاردة", "2026-08-31"),
        ("بكرة", "2026-09-01"),
        ("بكره", "2026-09-01"),
        ("bokra", "2026-09-01"),
        ("tomorrow", "2026-09-01"),
        ("بعد بكرة", "2026-09-02"),
        ("الأربع", "2026-09-02"),
        ("الاربع", "2026-09-02"),
        ("يوم الأربع", "2026-09-02"),
        ("on Wednesday", "2026-09-02"),
        ("الخميس", "2026-09-03"),
        ("الجمعة", "2026-09-04"),
        ("السبت", "2026-09-05"),
    ],
)
def test_a_day_a_patient_can_name_resolves_to_that_day(written: str, expected: str) -> None:
    assert parse_date(written, today=TODAY) == expected


def test_a_weekday_names_the_next_one_never_today() -> None:
    """Monday, said on a Monday, is the Monday coming.

    Same-day booking is out of the demo (decision 1), so reading "الاتنين" as today would offer
    a patient the one day the diary has nothing to give them.
    """
    assert parse_date("الاتنين", today=TODAY) == "2026-09-07"


@pytest.mark.parametrize(
    "written",
    [
        "",
        "next month",
        "الشهر الجاي",
        "after Eid",
        "2026-09-02 to 2026-09-04",
        "2026-02-31",
        "sometime next week",
    ],
)
def test_a_date_that_cannot_be_pinned_to_one_day_is_dropped(written: str) -> None:
    """The receptionist asks. It does not guess, and it does not book what it guessed."""
    assert parse_date(written, today=TODAY) is None


def test_a_date_in_the_past_is_refused_however_confidently_the_model_wrote_it() -> None:
    """A model resolving "Wednesday" against its training clock produces a real, wrong ISO date.

    It parses; it is simply last year. Taking it on trust is how a patient is confirmed into a
    day that has already been.
    """
    assert parse_date("2025-09-02", today=TODAY) is None
    assert parse_date("2026-08-25", today=TODAY) is None


def test_yesterday_is_allowed_one_day_of_slack() -> None:
    """A message sent at 23:58 and classified at 00:02 is not a model getting the year wrong."""
    assert parse_date("2026-08-30", today=TODAY) == "2026-08-30"


def test_a_date_years_out_is_refused() -> None:
    assert parse_date("2031-09-02", today=TODAY) is None


# ── times ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("18:00", "18:00"),
        ("6pm", "18:00"),
        ("6 pm", "18:00"),
        ("٦ مساء", "18:00"),
        ("الساعة ٦ مساءً", "18:00"),
        ("11:30", "11:30"),
        ("الساعة ١١ ص", "11:00"),
        ("9am", "09:00"),
        ("12 am", "00:00"),
    ],
)
def test_a_time_reads_as_a_24_hour_clock(written: str, expected: str) -> None:
    assert parse_time(written) == expected


@pytest.mark.parametrize(
    ("written", "expected"), [("6", "18:00"), ("6:30", "18:30"), ("7", "19:00")]
)
def test_a_bare_single_digit_hour_reads_as_the_afternoon(written: str, expected: str) -> None:
    """The clinic opens at 11:00. "الساعة ٦" is six in the evening, and nobody means 6am."""
    assert parse_time(written) == expected


def test_a_two_digit_hour_is_taken_as_written() -> None:
    """ "06:30" is somebody writing a 24-hour clock. Overriding an explicit form is its own bug."""
    assert parse_time("06:30") == "06:30"


def test_the_afternoon_assumption_moves_with_the_tenants_opening_hours() -> None:
    assert parse_time("6", assume_pm_before_hour=0) == "06:00"


@pytest.mark.parametrize("written", ["", "morning", "بدري", "25:00", "6:75"])
def test_a_time_that_is_not_one_is_dropped(written: str) -> None:
    assert parse_time(written) is None


# ── the filter ─────────────────────────────────────────────────────────────────────────────


def test_a_booking_message_fills_every_required_slot_in_one_turn() -> None:
    """The failure this whole step exists to remove: three details given, three details kept."""
    resolved = normalise_slots(
        "booking_enquiry",
        {
            "service": "ليزر",
            "branch": "الشيخ زايد",
            "requested_date": "بكرة",
            "requested_time": "٦",
        },
        vocabulary=CLINICS,
        today=TODAY,
    )
    assert resolved == {
        "service": "ليزر",
        "branch": "الشيخ زايد",
        "requested_date": "2026-09-01",
        "requested_time": "18:00",
    }


def test_a_slot_the_intent_does_not_declare_never_reaches_the_task() -> None:
    """The vocabulary decides what an intent may collect, not the model.

    ``patient_age`` is not a slot any clinic intent declares, and the clinical boundary in that
    vocabulary is partly a statement about what is never asked for or kept.
    """
    resolved = normalise_slots(
        "booking_enquiry",
        {"service": "فيلر", "patient_age": "34", "medical_history": "روأكيوتان"},
        vocabulary=CLINICS,
        today=TODAY,
    )
    assert resolved == {"service": "فيلر"}


def test_a_slot_from_another_intent_is_dropped() -> None:
    """``price_enquiry`` collects no date. A model reaching for the nearest key is not evidence."""
    resolved = normalise_slots(
        "price_enquiry",
        {"service": "هايدرافيشل", "requested_date": "بكرة"},
        vocabulary=CLINICS,
        today=TODAY,
    )
    assert resolved == {"service": "هايدرافيشل"}


@pytest.mark.parametrize("written", ["null", "None", "unknown", "N/A", "", "   "])
def test_the_words_models_write_instead_of_omitting_a_key_are_absences(written: str) -> None:
    resolved = normalise_slots(
        "booking_enquiry", {"service": written}, vocabulary=CLINICS, today=TODAY
    )
    assert resolved == {}


def test_an_unresolvable_date_is_dropped_and_the_rest_is_kept() -> None:
    """Losing the date costs a question. Guessing it costs the appointment."""
    resolved = normalise_slots(
        "booking_enquiry",
        {"service": "بوتوكس", "branch": "المعادي", "requested_date": "بعد العيد"},
        vocabulary=CLINICS,
        today=TODAY,
    )
    assert resolved == {"service": "بوتوكس", "branch": "المعادي"}


def test_an_intent_outside_this_tenants_vocabulary_yields_nothing() -> None:
    """A cross-vertical label already hands off; it must not also fill a task on the way there."""
    assert (
        normalise_slots("access_code_request", {"unit": "1204"}, vocabulary=CLINICS, today=TODAY)
        == {}
    )


def test_a_value_that_is_not_a_string_is_dropped() -> None:
    """Constrained decoding says ``dict[str, str]``; a gateway in between may not enforce it."""
    resolved = normalise_slots(
        "booking_enquiry",
        {"service": ["ليزر", "فيلر"], "branch": 3},  # type: ignore[dict-item]
        vocabulary=CLINICS,
        today=TODAY,
    )
    assert resolved == {}


def test_the_holiday_home_vertical_still_normalises_its_own_dates() -> None:
    resolved = normalise_slots(
        "booking_enquiry",
        {"check_in": "2026-09-04", "check_out": "tomorrow", "guests": "4"},
        vocabulary=HOLIDAY_HOMES,
        today=TODAY,
    )
    assert resolved == {"check_in": "2026-09-04", "check_out": "2026-09-01", "guests": "4"}


# ── the temporal provenance guard (pre-demo Step 3) ──────────────────────────────────────────


def test_a_date_the_message_does_not_state_is_dropped() -> None:
    """The live failure: a classifier reports a date the patient never wrote. It must not pass."""
    guarded = strip_unsupported_temporal_slots(
        {"service": "فاشيال", "branch": "المعادي", "requested_date": "2026-09-02"},
        "المواعيد المتاحة ايه",
        today=TODAY,
    )
    assert guarded == {"service": "فاشيال", "branch": "المعادي"}


def test_a_date_the_message_states_as_a_standalone_word_is_kept() -> None:
    guarded = strip_unsupported_temporal_slots(
        {"requested_date": "2026-09-01"}, "بكرة", today=TODAY
    )
    assert guarded == {"requested_date": "2026-09-01"}


def test_a_date_stated_inside_a_longer_sentence_is_kept() -> None:
    """A relative day the parser only knows as a whole value is still found in a sentence."""
    guarded = strip_unsupported_temporal_slots(
        {"service": "ليزر", "branch": "الشيخ زايد", "requested_date": "2026-09-01"},
        "عاوزة أحجز ليزر في الشيخ زايد بكرة",
        today=TODAY,
    )
    assert guarded["requested_date"] == "2026-09-01"


def test_a_multi_word_relative_day_in_a_sentence_is_kept() -> None:
    guarded = strip_unsupported_temporal_slots(
        {"requested_date": "2026-09-02"}, "احجزيلي بعد بكرة", today=TODAY
    )
    assert guarded == {"requested_date": "2026-09-02"}


def test_a_date_that_resolves_to_a_different_day_than_the_message_is_dropped() -> None:
    """A message says tomorrow; a fabricated value says a different day. The value loses."""
    guarded = strip_unsupported_temporal_slots(
        {"requested_date": "2026-09-05"}, "بكرة", today=TODAY
    )
    assert guarded == {}


def test_a_time_the_message_states_is_kept_and_a_fabricated_one_is_dropped() -> None:
    kept = strip_unsupported_temporal_slots(
        {"requested_time": "18:00"}, "احجزيلي الساعة ٦", today=TODAY
    )
    assert kept == {"requested_time": "18:00"}

    dropped = strip_unsupported_temporal_slots(
        {"requested_time": "18:00"}, "المواعيد المتاحة ايه", today=TODAY
    )
    assert dropped == {}


def test_service_and_branch_are_never_touched_by_the_temporal_guard() -> None:
    """The guard is temporal only: earlier-established, non-temporal slots pass through intact."""
    guarded = strip_unsupported_temporal_slots(
        {"service": "فاشيال", "branch": "المعادي"}, "المواعيد المتاحة ايه", today=TODAY
    )
    assert guarded == {"service": "فاشيال", "branch": "المعادي"}


def test_an_empty_message_drops_any_temporal_slot() -> None:
    guarded = strip_unsupported_temporal_slots(
        {"requested_date": "2026-09-02", "requested_time": "18:00", "service": "فاشيال"},
        None,
        today=TODAY,
    )
    assert guarded == {"service": "فاشيال"}


# ── temporal provenance: a number that is not a time (Codex remediation) ─────────────────────


@pytest.mark.parametrize(
    "message",
    [
        # originally reported
        "6 أكتوبر",
        "6 جلسات",
        "جلسة رقم 6",
        # the substring mechanism the fix closes: a bare number beside a word that merely *starts*
        # with a meem or saad, which the old " م"/" ص" markers matched anywhere.
        "6 مناطق",
        "6 مرات",
        "6 مرضى",
        "6 مواعيد",
        "6 صور",
        # a real time word elsewhere in the message no longer licenses an unrelated count.
        "مساء الخير، عايزة 6 جلسات",
    ],
)
def test_a_bare_number_that_is_not_a_time_drops_a_fabricated_requested_time(message: str) -> None:
    """The blocker: ``parse_time`` reads the "6" out of each of these as 18:00. Provenance must not.

    Every case carries a bare number that is not the patient stating an appointment time — a day, a
    count, an ordinal, or a number next to a word that only happens to begin with a time letter — so
    a classifier-invented ``requested_time`` gets no support and is dropped.
    """
    guarded = strip_unsupported_temporal_slots(
        {"service": "فاشيال", "requested_time": "18:00"}, message, today=TODAY
    )
    assert guarded == {"service": "فاشيال"}


def test_a_mixed_message_ties_the_value_to_the_bounded_span_not_a_stray_number() -> None:
    """Codex blocker: "6 جلسات الساعة 8" states 08:00 via "الساعة 8", not 18:00 from the count "6".

    A fabricated ``requested_time=18:00`` — what greedy ``parse_time`` reads from the leading "6" —
    must be dropped, because no bounded span in the message resolves to 18:00. The genuinely-stated
    08:00 (from the bounded "الساعة 8") is kept.
    """
    dropped = strip_unsupported_temporal_slots(
        {"service": "فاشيال", "requested_time": "18:00"}, "6 جلسات الساعة 8", today=TODAY
    )
    assert dropped == {"service": "فاشيال"}

    kept = strip_unsupported_temporal_slots(
        {"requested_time": "08:00"}, "6 جلسات الساعة 8", today=TODAY
    )
    assert kept == {"requested_time": "08:00"}


@pytest.mark.parametrize("message", ["6 مساء", "6 م", "6 ص", "6 صباح"])
def test_arabic_meridiem_words_are_not_accepted_as_time_markers(message: str) -> None:
    """Decision: only the English am/pm mark a number as a time; the Arabic meridiem words do not.

    "6 مساء" states 18:00 to a human, so the guard dropping it is a deliberate false-negative — a
    known Egyptian-Arabic limitation accepted for the demo (downstream, the receptionist re-asks, or
    hands off on the active-offer ``unclear`` path). Kept as an explicit test so the choice is
    visible and not re-widened by accident.
    """
    guarded = strip_unsupported_temporal_slots({"requested_time": "18:00"}, message, today=TODAY)
    assert guarded == {}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("الساعة 6", "18:00"),
        ("الساعة ٦", "18:00"),
        ("6:00", "18:00"),
        ("18:00", "18:00"),
        ("٦:٠٠", "18:00"),
        ("6pm", "18:00"),
        ("6 pm", "18:00"),
        ("9am", "09:00"),
        ("12 am", "00:00"),
    ],
)
def test_an_explicit_time_expression_survives_provenance(message: str, expected: str) -> None:
    guarded = strip_unsupported_temporal_slots({"requested_time": expected}, message, today=TODAY)
    assert guarded == {"requested_time": expected}


@pytest.mark.parametrize("message", ["6", "٦", "6.", "  ٦ ", "18"])
def test_a_bare_hour_as_the_whole_answer_survives_provenance(message: str) -> None:
    """A bare hour is a real answer when it is effectively all the patient said (6/٦/18 → 18:00)."""
    guarded = strip_unsupported_temporal_slots({"requested_time": "18:00"}, message, today=TODAY)
    assert guarded == {"requested_time": "18:00"}


def test_declared_slots_is_the_union_of_required_and_optional() -> None:
    assert declared_slots("booking_enquiry", CLINICS) >= {"service", "branch", "requested_date"}
    assert declared_slots("no_such_intent", CLINICS) == frozenset()


def test_every_shipped_date_or_time_slot_is_one_this_module_knows_about() -> None:
    """The drift guard.

    ``DATE_SLOTS`` and ``TIME_SLOTS`` are named lists, so a vocabulary that adds
    ``preferred_date`` would have it pass through as free text and reach a booking unresolved.
    Anything that reads like a date or a time has to be declared here, or listed in
    ``FREE_TEXT_DATE_SLOTS`` as a deliberate exception with its reason.
    """
    looks_temporal = {
        slot
        for vocab in shipped_vocabularies().values()
        for intent in vocab.intents
        for slot in (*intent.required_slots, *intent.optional_slots)
        if slot.endswith(("_date", "_time")) or slot in {"check_in", "check_out"}
    }
    known = DATE_SLOTS | TIME_SLOTS | FREE_TEXT_DATE_SLOTS
    assert looks_temporal <= known, f"undeclared date/time slots: {sorted(looks_temporal - known)}"


def test_a_backward_looking_date_is_left_as_the_patient_wrote_it() -> None:
    """``lost_property`` asks when you were here, and the answer is in the past by construction."""
    resolved = normalise_slots(
        "lost_property", {"visit_date": "الثلاثاء اللي فات"}, vocabulary=CLINICS, today=TODAY
    )
    assert resolved == {"visit_date": "الثلاثاء اللي فات"}
