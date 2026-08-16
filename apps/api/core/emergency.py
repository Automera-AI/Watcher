"""Is this a person in danger? Checked before intent, before confidence, before anything (G3).

The vocabulary has declared six emergency triggers, an action and an alert since item 0.3, and
until this file nothing read them. ``orchestration/worker.py`` passed ``emergency=False`` as a
literal, with a comment saying so. The consequence was not silence — it was worse than silence,
because A5 taught the system to answer: a guest typing *smell of gas* got a confident, polite
reply about maintenance while nobody was told.

This module is the detector. It takes the text of one inbound message and returns the trigger it
matched, or ``None``. It decides nothing else — the reply, the alert and the filing are the
orchestrator's, and the ceiling is ``core/autonomy.py``'s, which has taken an ``emergency`` flag
since 1.2 and short-circuits on it.

**The bias is deliberate and it is one-directional.** A false positive costs a phone call to an
operator about a guest who said "the fireplace smells". A false negative costs the thing the whole
item exists to prevent. Where the two trade off — matching loosely or matching tightly — this
matches loosely, and every choice below that looks careless is that trade being taken on purpose:

* **Arabic phrases match as substrings; Latin phrases match on word boundaries.** Arabic attaches
  its articles and conjunctions to the front of the word — *حريق* (fire) is *الحريق* with the
  article and *والحريق* with "and" — so a boundary test in Arabic invents false negatives on the
  most ordinary way to write the sentence. Latin does not agglutinate like that, and matching
  *fire* as a substring fires on "fireplace", "fireworks" and "campfire", which is a false positive
  with no compensating catch. Two scripts, two rules, for a reason that is a fact about the scripts.
* **Diacritics, alef forms and ta marbuta are normalised away** on both sides. A guest typing
  *ريحه غاز* rather than *ريحة غاز* is a guest with a gas leak.
* **Digits are letters here.** Franco-Arabic spells sounds with numerals — *re7et ghaz*, *7ari2* —
  so the boundary class includes them, and ``7ari2`` does not match inside ``x7ari2y``.

**The one trigger that is not a phrase.** ``locked_out_at_night`` carries ``only_between``, and it
is the reason this function needs a clock and a timezone: locked out at 2pm is a support request,
locked out at 2am is a person on a street. The window is read in the tenant's local time, not the
server's — ``TenantPolicy.timezone``, which defaults to the first market's zone and is set per
deploy. A message timestamp without a timezone is read as UTC, because that is what every channel
adapter in this tree produces and guessing anything else would move the window by hours.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, time
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from packages.intents.schema import EmergencyTrigger, Vocabulary, default_vocabulary

#: What the guest is told, immediately, whatever the trigger. The vocabulary declares
#: ``reply_immediately: true`` and says nothing about wording, so the wording is here.
#:
#: Bilingual in one message rather than one language chosen by a detector. We know which *phrase*
#: matched, not which language the guest reads — a Franco-Arabic trigger is typed by someone who
#: may prefer either — and a safety line is the last place to spend a guess. It is also the reason
#: this text names no emergency number: 999 in the UAE and 122 in Egypt are per-market facts that
#: belong to a client's configuration, and a wrong number printed with confidence is worse than
#: "your local emergency number".
EMERGENCY_REPLY = (
    "This sounds like an emergency. I am alerting someone from the team right now — they will "
    "contact you immediately. If anyone is in danger, please call your local emergency number "
    "first.\n\n"
    "يبدو أن هذه حالة طارئة. أنا أبلغ أحد أفراد الفريق الآن وسيتصل بك فوراً. "
    "إذا كان أي شخص في خطر، اتصل برقم الطوارئ المحلي أولاً."
)

#: The default market's zone. AE and EG are the two declared markets; Dubai is where the first
#: client will be. It is a *default*, not an assumption baked into the detector — see
#: ``TenantPolicy.timezone``, which is what actually reaches this module.
DEFAULT_TIMEZONE = "Asia/Dubai"

#: Latin letters and digits. Digits are in here because Franco-Arabic spells ع, ح and ء as 3, 7
#: and 2, so they are letters in the only sense this boundary cares about.
_LATIN_WORD_CHARS = "0-9a-z"

#: Arabic short vowels, sukun, shadda, the dagger alef and tatweel — decoration that changes how a
#: word is pronounced and never what it means. Stripped from both the message and the trigger.
#: Written as code points because they are combining marks: pasted literally they attach
#: to the bracket and the character class becomes unreadable in an editor.
_ARABIC_MARKS = re.compile("[\\u0610-\\u061a\\u064b-\\u065f\\u0670\\u06d6-\\u06dc\\u0640]")

#: Letters with more than one accepted spelling. Folded to one on both sides of the comparison.
_ARABIC_FOLD = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
    }
)

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class EmergencyDetection:
    """One matched trigger: what fired, on what, and what the vocabulary says to do about it.

    ``action`` and ``alert`` are carried rather than looked up again downstream so that the thing
    which reads the vocabulary and the thing which acts on it cannot drift — the alerter is handed
    the channel the file asked for, and can say plainly when it is not the channel it delivered on.
    """

    trigger_id: str
    matched: str
    """The declared phrase that fired, verbatim from ``intents.yaml`` — not the guest's words."""

    action: str
    alert: str

    def snapshot(self) -> dict[str, Any]:
        """The audit/inbox snapshot for this detection.

        Deliberately the same shape a classification snapshot has — a flat dict the control page
        can render — and deliberately *not* the guest's message text, which is already on the
        ``messages`` row this item points at.
        """
        return {
            "emergency": True,
            "trigger_id": self.trigger_id,
            "matched_phrase": self.matched,
            "action": self.action,
            "alert": self.alert,
        }


def normalise(text: str) -> str:
    """Fold a string to the form both sides of a comparison are written in.

    NFKC first (so a presentation-form Arabic letter becomes the ordinary one), then case, then
    the Arabic marks and letter variants, then whitespace. Punctuation is left alone: it is what
    the Latin boundary test reads as "not a word character".
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _ARABIC_MARKS.sub("", folded).translate(_ARABIC_FOLD)
    return _WHITESPACE.sub(" ", folded).strip()


def _is_latin(phrase: str) -> bool:
    """Whether a trigger phrase is written in Latin letters.

    The vocabulary's own validator refuses a phrase that mixes alphabets, so every phrase is one
    or the other and the first letter settles it. Franco-Arabic is Latin by this test, which is
    correct: it is typed with a Latin keyboard and spaced like Latin.
    """
    for ch in phrase:
        if ch.isalpha():
            return "ARABIC" not in unicodedata.name(ch, "")
    return True


@lru_cache(maxsize=256)
def _pattern(phrase: str) -> re.Pattern[str]:
    """The matcher for one declared phrase, compiled once.

    Words are joined with ``\\s+`` rather than a literal space so that "gas   leak" and a line
    break between the two words both match — a guest in a hurry types badly.
    """
    body = r"\s+".join(re.escape(word) for word in normalise(phrase).split())
    if _is_latin(phrase):
        return re.compile(rf"(?<![{_LATIN_WORD_CHARS}]){body}(?![{_LATIN_WORD_CHARS}])")
    return re.compile(body)


@lru_cache(maxsize=8)
def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def timezone_is_known(name: str) -> bool:
    """Whether ``name`` is a zone this machine can resolve.

    Asked by the configuration layer at startup, because the alternative is discovering that
    ``TENANT_TIMEZONE`` is misspelled at 2am, on the one trigger that needs a clock.
    """
    try:
        _zone(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _within_window(window: list[str], at: datetime, timezone: str) -> bool:
    """Whether ``at``, in the tenant's local time, falls inside ``["22:00", "07:00"]``.

    Wraps midnight, which is the only interesting case: the window that matters runs from the
    evening into the following morning, so ``start > end`` is the normal shape rather than an
    error to reject.
    """
    start, end = (time.fromisoformat(value) for value in window)
    moment = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    local = moment.astimezone(_zone(timezone)).time()
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def _fires(trigger: EmergencyTrigger, text: str, at: datetime, timezone: str) -> str | None:
    """The declared phrase that matched, or ``None``. Applies ``only_between`` if it is set."""
    if trigger.only_between is not None and not _within_window(trigger.only_between, at, timezone):
        return None
    for phrase in trigger.any_of:
        if _pattern(phrase).search(text):
            return phrase
    return None


def detect(
    text: str | None,
    *,
    at: datetime,
    timezone: str = DEFAULT_TIMEZONE,
    vocabulary: Vocabulary | None = None,
) -> EmergencyDetection | None:
    """The emergency this message is, or ``None``.

    ``text`` is the message's classifiable text, which for a voice note is the transcript — so
    this must run *after* the media pipeline and before everything else. A message with no text at
    all (an image nothing could read) is not an emergency this function can see, and says so by
    returning ``None`` rather than guessing.

    Triggers are checked in declaration order and the first match wins. There is no scoring and no
    threshold: a phrase an operator wrote down in ``intents.yaml`` is present or it is not, and
    that is the property that makes this reviewable by the person who owns the consequences.
    """
    if not text or not text.strip():
        return None

    vocab = vocabulary or default_vocabulary()
    emergency = vocab.emergency
    normalised = normalise(text)

    for trigger in emergency.triggers:
        if (matched := _fires(trigger, normalised, at, timezone)) is not None:
            return EmergencyDetection(
                trigger_id=trigger.id,
                matched=matched,
                action=emergency.action,
                alert=emergency.alert,
            )
    return None
