"""What the model extracted, turned into values a task can act on (demo step 5).

``worker.py`` passed ``{}`` for extracted slots from the day the receptionist was wired, and every
comment that mentioned it called the gap "item 2.x". The consequence was not a missing feature: a
task collects its required slots before it may execute, so with nothing ever absorbed the
receptionist asked for the same detail every turn until ``max_clarifying_turns`` fetched a person.
"عاوزة أحجز ليزر في الشيخ زايد يوم الأربع" — which supplies all three of ``booking_enquiry``'s
required slots — got asked for the service, then asked for the service again, then handed off.

This module is the half of the fix that is not a prompt. The model reads a message and copies out
what it says; this decides which of those keys are real and what their values mean. The split
matters because the two fail differently. A model that mislabels an intent is measured by the eval
set. A model that emits ``{"requested_date": "الأربع"}`` is not wrong — it is *unresolved*, and
resolving it is arithmetic against a calendar, which is the kind of thing that should be done in
code that can be tested against a fixed today rather than re-derived by a language model at 15:03
on demo day.

**Three rules, and the third is the one that matters.**

*Only declared slots.* A key the chosen intent does not declare is dropped. The vocabulary is the
list of details an intent is allowed to collect, and a model that invents ``patient_age`` for a
booking must not be able to put it in a task — the clinical boundary in the clinic vocabulary is
partly a statement about what is never asked for or stored.

*Only resolvable values.* ``null``, ``unknown`` and an empty string are absences, not values, and
several models write them out rather than omitting the key.

*A date that cannot be pinned down is dropped, never guessed.* A dropped date makes the
receptionist ask "which day?", which is a good turn. A wrong date books a patient into the wrong
day and tells them it is confirmed. These are not close in cost, so every ambiguity here resolves
towards asking.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, timedelta

from packages.intents.schema import Vocabulary

from apps.api.core.emergency import normalise

#: Slots whose value is a calendar date. Named rather than inferred from the suffix because
#: ``check_in`` and ``check_out`` carry dates and say nothing about it — the suffix rule would
#: silently pass a holiday-home guest's arrival date through as free text. ``test_slots.py``
#: asserts that no shipped vocabulary declares a date- or time-looking slot missing from these.
DATE_SLOTS = frozenset({"requested_date", "check_in", "check_out", "new_check_out"})

#: Slots whose value is a wall-clock time.
TIME_SLOTS = frozenset({"requested_time", "arrival_time"})

#: Date-shaped slots deliberately left as the patient's own words. ``visit_date`` is the day
#: somebody *was* at the clinic and left a bag behind, so both rules here point the wrong way: the
#: weekday resolver returns the next Tuesday, and the range check refuses a date in the past. A
#: past-facing resolver is real work for a slot whose intent hands the message to a person to
#: read, so this is a decision rather than an oversight, and ``test_slots.py`` asserts the list.
FREE_TEXT_DATE_SLOTS = frozenset({"visit_date"})

#: How a resolved date is written into a task, and what step 6 parses back out of one.
DATE_FORMAT = "%Y-%m-%d"

#: A bare hour below this reads as the afternoon. "احجزيلي الساعة ٦" is six in the evening: the
#: clinic opens at 11:00 and no patient asks for a 6am appointment. A parameter rather than a
#: constant because it is a fact about a tenant's opening hours, and a tenant that opens at 07:00
#: needs it lower.
ASSUME_PM_BEFORE_HOUR = 8

#: A resolved date this far before today is a model resolving "Wednesday" against its own training
#: clock rather than this conversation. One day of slack, because a message sent at 23:58 can be
#: classified at 00:01 and "today" is still yesterday's date to the person who typed it.
_MAX_BACKDATE_DAYS = 1

#: And this far ahead is the same failure in the other direction. A clinic diary is loaded a week
#: at a time; a date fourteen months out is a mis-parse, not a patient planning ahead.
_MAX_FORWARD_DAYS = 366

#: Words models write instead of leaving a key out.
_ABSENCES = frozenset({"null", "none", "nil", "unknown", "n/a", "na", "-", "--", "?"})

#: Arabic-Indic and extended Arabic-Indic digits. NFKC does not fold these, so they arrive as
#: written: "الساعة ٦" is a perfectly ordinary way to type a time.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_CLOCK = re.compile(r"(\d{1,2})\s*[:.٫]\s*(\d{2})")
_BARE_HOUR = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")

#: Days from today, by what the patient called it. Keys are already folded by ``normalise``.
_RELATIVE_DAYS: dict[str, int] = {
    "today": 0,
    "tonight": 0,
    "this evening": 0,
    "النهارده": 0,
    "النهاردة": 0,
    "اليوم": 0,
    "الليله": 0,
    "الليلة": 0,
    "elnaharda": 0,
    "enaharda": 0,
    "el naharda": 0,
    "tomorrow": 1,
    "بكره": 1,
    "بكرة": 1,
    "غدا": 1,
    "bokra": 1,
    "bukra": 1,
    "day after tomorrow": 2,
    "the day after tomorrow": 2,
    "بعد بكره": 2,
    "بعد بكرة": 2,
    "بعد غد": 2,
    "ba3d bokra": 2,
    "ba3d bukra": 2,
}

#: Weekday, Monday=0, by name. Egyptian Arabic writes most of these without the final taa marbuta
#: — "الأربع" for Wednesday — which is what a patient types and what the model copies out.
_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "الاتنين": 0,
    "الإثنين": 0,
    "الاثنين": 0,
    "etnen": 0,
    "eletnen": 0,
    "tuesday": 1,
    "tue": 1,
    "التلات": 1,
    "الثلاثاء": 1,
    "التلاتاء": 1,
    "talat": 1,
    "eltalat": 1,
    "wednesday": 2,
    "wed": 2,
    "الاربع": 2,
    "الأربع": 2,
    "الاربعاء": 2,
    "الأربعاء": 2,
    "arbe3": 2,
    "elarbe3": 2,
    "thursday": 3,
    "thu": 3,
    "الخميس": 3,
    "khamees": 3,
    "elkhamees": 3,
    "friday": 4,
    "fri": 4,
    "الجمعه": 4,
    "الجمعة": 4,
    "gom3a": 4,
    "elgom3a": 4,
    "saturday": 5,
    "sat": 5,
    "السبت": 5,
    "sabt": 5,
    "elsabt": 5,
    "sunday": 6,
    "sun": 6,
    "الاحد": 6,
    "الأحد": 6,
    "7ad": 6,
    "elhad": 6,
}

#: Markers that settle a 12-hour clock. Checked against the folded text.
_PM_MARKERS = ("pm", "p.m", "مساء", "مساءا", "مسائا", "بالليل", "العصر", "الضهر", "الظهر", " م")
_AM_MARKERS = ("am", "a.m", "صباحا", "صباح", "الصبح", " ص")


def _fold(text: str) -> str:
    """The form both sides of a lookup are written in: Arabic marks folded, digits Latinised."""
    return normalise(text.translate(_DIGITS))


def parse_date(text: str, *, today: date) -> str | None:
    """The calendar date ``text`` means, as ``YYYY-MM-DD``, or ``None`` if it cannot be pinned.

    Three forms, in order of how much is being trusted to the model: an ISO date it resolved
    itself, a relative day ("بكرة"), and a weekday name resolved to its next occurrence. Anything
    else — "next month", "after Eid", a range — is ``None``, and the receptionist asks.

    An ISO date is *range-checked*, not taken on trust. A model given today's date still
    occasionally resolves "Wednesday" against the calendar it was trained on, and a date in the
    past reaching a booking is how a patient is confirmed into a day that has been and gone.
    """
    folded = _fold(text)
    if not folded:
        return None

    iso = _ISO_DATE.match(folded)
    if iso is not None:
        try:
            found = date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:  # 2026-02-31 and friends.
            return None
        return _within_range(found, today)

    if (offset := _RELATIVE_DAYS.get(folded)) is not None:
        return (today + timedelta(days=offset)).strftime(DATE_FORMAT)

    weekday = _WEEKDAYS.get(folded)
    if weekday is None:
        # "يوم الأربع", "on Wednesday", "الأربع الجاي" — the day name with a word either side.
        for word in folded.split():
            if (found_weekday := _WEEKDAYS.get(word)) is not None:
                weekday = found_weekday
                break
    if weekday is None:
        return None

    # The *next* occurrence, and never today. A patient naming a weekday on that weekday means
    # the one coming, and same-day booking is out of the demo anyway (decision 1).
    ahead = (weekday - today.weekday()) % 7 or 7
    return (today + timedelta(days=ahead)).strftime(DATE_FORMAT)


def _within_range(found: date, today: date) -> str | None:
    if not -_MAX_BACKDATE_DAYS <= (found - today).days <= _MAX_FORWARD_DAYS:
        return None
    return found.strftime(DATE_FORMAT)


def parse_time(text: str, *, assume_pm_before_hour: int = ASSUME_PM_BEFORE_HOUR) -> str | None:
    """The wall-clock time ``text`` means, as 24-hour ``HH:MM``, or ``None``.

    Reads "6", "6:30", "٦ مساء", "6pm" and "18:00". A time is never used to *choose* a slot on its
    own — ``check_availability`` returns what the diary actually holds — so a value here narrows a
    search and is not, by itself, something a patient can be booked into.
    """
    folded = _fold(text)
    if not folded:
        return None

    clock = _CLOCK.search(folded)
    if clock is not None:
        written_hour, minute = clock[1], int(clock[2])
    else:
        bare = _BARE_HOUR.search(folded)
        if bare is None:
            return None
        written_hour, minute = bare[1], 0
    hour = int(written_hour)

    if minute > 59 or hour > 24:
        return None

    padded = f" {folded} "
    if any(marker in padded for marker in _PM_MARKERS):
        if hour < 12:
            hour += 12
    elif any(marker in padded for marker in _AM_MARKERS):
        if hour == 12:
            hour = 0
    elif len(written_hour) == 1 and 0 < hour < assume_pm_before_hour:
        # A single-digit hour with nothing to settle it — "6", "6:30". See
        # ``ASSUME_PM_BEFORE_HOUR``. A two-digit hour is left alone: "06:30" is somebody writing a
        # 24-hour clock, and overriding an explicit form is a different mistake from resolving an
        # ambiguous one.
        hour += 12

    if hour == 24:
        hour = 0
    if hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def declared_slots(intent: str, vocabulary: Vocabulary) -> frozenset[str]:
    """Every slot the vocabulary lets ``intent`` collect. Empty for an intent it does not define.

    Empty rather than an exception: an intent outside this tenant's vocabulary already hands off
    (``decide_autonomy`` does not recognise it), and raising here would turn a cross-vertical label
    into a crash on the message path instead of the safe hand-off it is meant to be.
    """
    found = next((i for i in vocabulary.intents if i.name == intent), None)
    if found is None:
        return frozenset()
    return frozenset(found.required_slots) | frozenset(found.optional_slots)


def normalise_slots(
    intent: str,
    extracted: Mapping[str, str],
    *,
    vocabulary: Vocabulary,
    today: date,
    assume_pm_before_hour: int = ASSUME_PM_BEFORE_HOUR,
) -> dict[str, str]:
    """What of ``extracted`` the task for ``intent`` may absorb, in the forms step 6 reads.

    Silently lossy, and that is the intended shape: everything this drops is something the
    receptionist will ask for in its own words, which is a better turn than acting on a value
    nobody could resolve. Nothing here reaches a patient, so a drop costs a question and a wrong
    value costs an appointment.
    """
    declared = declared_slots(intent, vocabulary)
    if not declared:
        return {}

    resolved: dict[str, str] = {}
    for key, raw in extracted.items():
        name = key.strip()
        if name not in declared:
            continue
        if not isinstance(raw, str):  # A model that answered with a number or a nested object.
            continue
        text = " ".join(raw.split())
        if not text or _fold(text) in _ABSENCES:
            continue

        if name in DATE_SLOTS:
            value = parse_date(text, today=today)
        elif name in TIME_SLOTS:
            value = parse_time(text, assume_pm_before_hour=assume_pm_before_hour)
        else:
            value = text

        if value:
            resolved[name] = value
    return resolved
