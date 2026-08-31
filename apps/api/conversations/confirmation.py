"""Reading a short reply against the question that is actually pending (demo step 6).

The clinic vocabulary's header states this as a rule and nothing implemented it: *short replies
such as "تمام", "أيوه", "لا" are interpreted against an active pending question or confirmation
before the flat vocabulary is consulted.* Two things went wrong without it, and both of them end
the booking journey.

**Nothing ever agreed to anything.** ``Task.confirmed`` was a set that only ever had entries
*removed* from it. A task whose intent declares ``confirm_before_acting`` therefore read back a
detail, was told "أيوه", and read it back again, until ``max_clarifying_turns`` fetched a person.
The confirm branch of the receptionist could not be reached twice by the same task and come out
the other side, so ``confirm_booking`` was unreachable even once it existed.

**And "تمام" ends the conversation.** Classified flat, with no pending question in view, it is
``thanks_closing`` — a real, correct label for that word most of the time. Mid-booking it means
*yes, go ahead*, and the receptionist would abandon the task and say goodbye to somebody who was
one word away from an appointment. The handoff calls this the most likely live failure on demo
day, and it is: it needs no unusual phrasing, no edge case, and no bad luck.

**The ambiguity is resolved by context, not by the word.** Nothing here decides what "تمام" means
in general — the caller only asks when a confirmation is genuinely outstanding, and in that
position it is an answer. Away from one the flat vocabulary keeps it, and "تمام" still closes a
conversation. That is the whole content of the dialogue-state rule for this step; the general form,
covering every pending question rather than the confirmation, is still unbuilt.
"""

from __future__ import annotations

from apps.api.core.emergency import normalise

#: The most a reply can be and still be read as a bare answer. "أيوه احجزيلي" is an answer;
#: "أيوه بس ممكن نغير الميعاد لبكرة" is a new instruction and belongs to the flat vocabulary.
_MAX_WORDS = 4

#: Words that agree. "تمام" and "ماشي" are here for the reason the module docstring gives: they are
#: read as agreement *only* where a confirmation is pending, and this function is only asked there.
_YES = frozenset(
    {
        "أيوه",
        "ايوه",
        "أيوة",
        "ايوة",
        "اه",
        "آه",
        "أه",
        "نعم",
        "تمام",
        "ماشي",
        "حاضر",
        "ياريت",
        "أكيد",
        "اكيد",
        "اوك",
        "اوكي",
        "احجزي",
        "احجزيلي",
        "اححز",
        "aywa",
        "aiwa",
        "ah",
        "tamam",
        "mashy",
        "mashi",
        "akid",
        "yes",
        "yeah",
        "yep",
        "yup",
        "ok",
        "okay",
        "sure",
        "confirm",
        "confirmed",
        "book",
        "go",
        "ahead",
        "please",
        "correct",
        "right",
    }
)

#: Words that decline or correct. Checked first: a reply carrying any of these is not agreement,
#: whatever else it carries, because "لا تمام" is a refusal with a politeness word attached.
_NO = frozenset(
    {
        "لا",
        "لأ",
        "لاء",
        "مش",
        "غلط",
        "خطأ",
        "الغي",
        "ألغي",
        "la",
        "laa",
        "la2",
        "mesh",
        "mish",
        "no",
        "nope",
        "nah",
        "not",
        "wrong",
        "incorrect",
        "cancel",
        "change",
    }
)

#: Politeness and filler that neither agrees nor declines and must not stop a reply reading as
#: bare. "أيوه شكرا" is still yes.
_FILLER = frozenset(
    {
        "شكرا",
        "شكراً",
        "جدا",
        "خلاص",
        "طبعا",
        "طيب",
        "يا",
        "لو",
        "سمحت",
        "من",
        "فضلك",
        "it",
        "that",
        "is",
        "thanks",
        "thank",
        "you",
        "very",
        "much",
        "fine",
        "good",
        "great",
    }
)


def _words(text: str) -> list[str]:
    return [word for word in normalise(text).replace("،", " ").replace(",", " ").split() if word]


def reads_as_no(text: str) -> bool:
    """Whether a short reply declines or corrects what was read back to it.

    Deliberately looser than :func:`reads_as_yes`: any refusal word in a short reply is a refusal.
    Being wrong in this direction costs a clarifying question, and being wrong in the other
    direction books an appointment nobody agreed to.
    """
    words = _words(text)
    if not words or len(words) > _MAX_WORDS:
        return False
    return any(word in _NO for word in words)


def reads_as_yes(text: str) -> bool:
    """Whether a short reply agrees with what was read back to it.

    Two shapes count, and nothing else does. A reply made **entirely** of agreement and politeness
    ("أيوه", "تمام شكرا", "yes please") — and a short reply that **opens** with agreement
    ("أيوه احجزيلي"), because that is one sentence doing both and refusing it would ask the patient
    to say yes twice.
    """
    words = _words(text)
    if not words or len(words) > _MAX_WORDS or reads_as_no(text):
        return False
    if any(word in _YES for word in words) and all(word in _YES | _FILLER for word in words):
        return True
    return words[0] in _YES
