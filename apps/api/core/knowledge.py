"""Matching a guest's question to an answer (roadmap 2.4).

``answer_from_knowledge`` is the ``terminal_tool`` five intents declare — ``property_question``,
``check_in_support``, ``directions``, ``checkout_question``, ``general_info`` — and until this
item nothing implemented it: every one of them reached ``execute`` and got the same "All set!
I've noted everything down.", whether or not that was true. This is the lookup half of the fix;
``conversations/tools.py``'s ``AnswerFromKnowledge`` is the tool that calls it and
``conversations/receptionist.py`` is where the dispatch changed.

**Matching is fuzzy because slot extraction (item 2.x) does not exist.** ``property_question``
declares an optional ``topic`` slot for exactly this lookup, but the model emits no slots today —
``extracted_slots`` reaching the receptionist is always ``{}`` — so the only signal available is
the guest's raw message text. ``rapidfuzz`` scores it against each fact's ``question``, which is
written as the phrasing a guest would actually use ("what's the wifi password"), not against
``topic``, which is a human-facing grouping label for the knowledge view (roadmap D5).

**A fact's ``sensitive`` flag is enforced here, narrowly, and it is deliberately not G1.** G1
(roadmap track G, not yet built) is a reply-path-wide disclosure gate: it will eventually decide,
for every intent, what an unverified guest may be told. This module does one smaller thing —
refuses to let ``answer_from_knowledge`` hand a sensitive fact (a lockbox code, an alarm code) to
a guest nobody has vouched for. It does not attempt to verify anyone; it treats an unverified
match to a sensitive fact exactly like no match at all, which is what
``intents.yaml``'s ``defaults.on_no_knowledge: handoff_to_human`` already says to do with "I don't
know". Building the fuller gate — money and owner matters, ceilings a confident model cannot
raise — is still G1's job.

**A door code, a key box code or a unit number does not belong in this table at all**, sensitive
or not. ``intents.yaml`` forbids ``check_in_support`` from ever giving one out through
``answer_from_knowledge``, whatever ``identity_verified`` says — that disclosure is
``access_code_request``'s job, via ``lookup_reservation`` (roadmap 3.1, unimplemented), gated on
a real booking reference rather than a bool this tool is trusted with. Whoever populates a
tenant's facts (today: a script; eventually: the knowledge view, roadmap D5) should read that as
an exclusion, not a reason to set ``sensitive=True`` and move on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz

#: Below this score a guest's question is treated as unmatched rather than answered wrong. Not
#: high because it is tuned — there is no golden set for this yet (revisit at 2.7) — but because
#: a false "I don't know" costs one handoff and a false answer costs trust in the whole product.
MATCH_THRESHOLD = 60.0

#: Stripped from both sides before scoring. Every fact in a small knowledge base tends to be
#: phrased as "is there a ___" / "what's the ___", and scoring the *whole* sentence lets that
#: shared scaffolding outweigh the one word that actually distinguishes "is there parking" from
#: "is there a garden" — a guest asking about parking scored the same against both. Stripping
#: these first means a match is won or lost on the words that name the thing being asked about.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "there",
        "do",
        "does",
        "have",
        "has",
        "how",
        "what",
        "whats",
        "to",
        "of",
        "for",
        "and",
        "can",
        "we",
        "i",
        "it",
        "in",
        "on",
        "at",
        "me",
        "please",
        "you",
        "your",
        "my",
    }
)


def _content_words(text: str) -> str:
    """The words in ``text`` that are not in ``_STOPWORDS``, lowercased and joined.

    Falls back to the normalised-but-unfiltered text when nothing survives (a question that is
    *only* stopwords should still be scored on something rather than matching every fact via two
    empty strings).
    """
    cleaned = re.sub(r"[^a-z0-9\s']", " ", text.lower())
    words = [w for w in cleaned.split() if w not in _STOPWORDS]
    return " ".join(words) if words else cleaned


@dataclass(frozen=True, slots=True)
class Fact:
    """One answerable question in a tenant's knowledge base.

    ``sensitive`` is what ``best_match`` and ``AnswerFromKnowledge`` (``conversations/tools.py``)
    key their refusal on. ``topic`` groups facts for a human editing them (roadmap D5); it plays
    no part in matching.

    ``property_id`` scopes the fact to one unit (roadmap 2.8), or is ``None`` for a fact true of
    every property the tenant runs. It is carried here so a caller can tell the two apart, but it
    is the *repository* that decides which facts to return for a resolved property — ``best_match``
    scores whatever list it is handed and never reads this field.
    """

    id: str
    topic: str
    question: str
    answer: str
    sensitive: bool
    property_id: str | None = None


class KnowledgeLookup(Protocol):
    """Where a tenant's facts live. One implementation today: ``db.knowledge_repo``.

    ``property_id`` scopes the lookup to one unit (roadmap 2.8): the implementation returns
    tenant-wide facts (``property_id IS NULL``) *and* the named property's, never another
    property's. ``None`` — the caller could not resolve a property — means tenant-wide facts only.
    """

    def search(self, tenant_id: str, property_id: str | None = None) -> list[Fact]: ...


def best_match(guest_text: str, facts: list[Fact]) -> Fact | None:
    """The single best-matching fact for what the guest asked, or ``None`` below the floor.

    Ties and near-ties both resolve to whichever fact is scanned first — with no golden set yet
    to rank facts by, breaking a tie any other way would be inventing precision this does not
    have.
    """
    if not guest_text.strip():
        return None
    guest_words = _content_words(guest_text)
    best: Fact | None = None
    best_score = 0.0
    for fact in facts:
        # WRatio, not a plain ratio: a guest's "parking?" and a fact written as "is there
        # parking available at the property" describe the same thing at very different lengths,
        # and a metric that penalises the length gap would make writing a natural-sounding
        # knowledge base the wrong way to write one.
        score = fuzz.WRatio(guest_words, _content_words(fact.question))
        if score > best_score:
            best, best_score = fact, score
    if best is None or best_score < MATCH_THRESHOLD:
        return None
    return best
