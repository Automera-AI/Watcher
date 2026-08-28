"""Matching what a patient wrote against the clinic's own catalogue (demo step 6).

The importer already resolves names the *workbook* uses — an availability row saying "Basic
Facial" finds ``DT004`` because the sheet and the catalogue were written by the same person on the
same day. A patient is not that person. They write "فاشيال", "el facial", "the basic one", and
whatever they write has to reach a catalogue code before a price can be quoted or a slot offered.

**This module resolves; it never picks.** A message that reaches two services comes back as two
candidates, and the receptionist asks which — it does not choose the cheaper one, the first one, or
the one whose name is longest. That is the failure deviation 3 of the source-data review is about:
"Basic Facial" and "Facial" are both 750 EGP for 45 minutes, three different 12-session laser
packages all cost 16,350, and a receptionist that silently picks one quotes a real price for a
treatment the patient did not ask for. Same-price look-alikes make that *invisible* — the number is
right, the appointment is wrong.

**Bilingual matching is data, not code.** The catalogue is written in English and the demo's
patients write Egyptian Arabic. Nothing here transliterates or translates: a service answers to its
name and to its ``aliases``, both of which come from the workbook, so the Arabic a patient uses is
something the clinic writes down and can be held to. An Arabic term with no alias behind it
resolves to nothing, the receptionist asks, and the fix is a column in the client's own file rather
than a dictionary in this repository that nobody clinical has read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from apps.api.core.clinic import Branch, Service, normalise_service_name


@dataclass(frozen=True, slots=True)
class ServiceMatch:
    """What one piece of a patient's message reached in the catalogue.

    Exactly one of three states, and the caller has to handle all three: ``found`` is a single
    service, ``candidates`` is more than one and needs a question, and neither is set when nothing
    matched at all.
    """

    found: Service | None = None
    candidates: tuple[Service, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return self.found is None and len(self.candidates) > 1

    @property
    def missing(self) -> bool:
        return self.found is None and not self.candidates


@dataclass(frozen=True, slots=True)
class BranchMatch:
    """The same three states for a location. See :class:`ServiceMatch`."""

    found: Branch | None = None
    candidates: tuple[Branch, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return self.found is None and len(self.candidates) > 1

    @property
    def missing(self) -> bool:
        return self.found is None and not self.candidates


#: Words that carry no discriminating power in a service or branch name. Dropping them is what
#: lets "فرع الشيخ زايد" match the branch "Sheikh Zayed" — but the list stays short and
#: structural on purpose. Every word removed here is a word two different services are allowed to
#: differ by, and "6 sessions" versus "12 sessions" is the whole difference between two prices.
_NOISE = frozenset(
    {
        "the",
        "a",
        "at",
        "in",
        "of",
        "branch",
        "clinic",
        "session",
        "sessions",
        "فرع",
        "عياده",
        "عيادة",
        "جلسه",
        "جلسة",
        "جلسات",
        "في",
        "ال",
    }
)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(normalise_service_name(text).split()) - _NOISE


def _names_of_service(service: Service) -> tuple[str, ...]:
    return (service.code, *service.names)


def _names_of_branch(branch: Branch) -> tuple[str, ...]:
    return (branch.external_id, *branch.names, *(x for x in (branch.area,) if x))


def resolve_service(text: str, services: Sequence[Service]) -> ServiceMatch:
    """The catalogue item ``text`` names, the several it could name, or nothing.

    Two passes, and the order is the whole design. An **exact** name, alias or code match wins
    outright, so a patient who says exactly what the catalogue says is never dragged into a
    clarifying question by a longer name that happens to contain their words. Only when nothing
    matches exactly does the **token** pass run, and that one deliberately returns everything it
    reached: "ليزر" against a catalogue with eleven laser packages is a question, not a booking.
    """
    wanted = normalise_service_name(text)
    if not wanted:
        return ServiceMatch()

    exact = [
        s
        for s in services
        if any(normalise_service_name(n) == wanted for n in _names_of_service(s))
    ]
    if len(exact) == 1:
        return ServiceMatch(found=exact[0])
    if exact:
        return ServiceMatch(candidates=tuple(exact))

    tokens = _tokens(text)
    if not tokens:
        return ServiceMatch()
    reached = [s for s in services if any(tokens <= _tokens(n) for n in _names_of_service(s))]
    if len(reached) == 1:
        return ServiceMatch(found=reached[0])
    return ServiceMatch(candidates=tuple(reached))


def resolve_branch(text: str, branches: Sequence[Branch]) -> BranchMatch:
    """The location ``text`` names. Same two passes as :func:`resolve_service`.

    A branch also answers to its area, because "المعادي" is how a patient says which one they mean
    and the clinic's own name for it may be longer.
    """
    wanted = normalise_service_name(text)
    if not wanted:
        return BranchMatch()

    exact = [
        b for b in branches if any(normalise_service_name(n) == wanted for n in _names_of_branch(b))
    ]
    if len(exact) == 1:
        return BranchMatch(found=exact[0])
    if exact:
        return BranchMatch(candidates=tuple(exact))

    tokens = _tokens(text)
    if not tokens:
        return BranchMatch()
    reached = [b for b in branches if any(tokens <= _tokens(n) for n in _names_of_branch(b))]
    if len(reached) == 1:
        return BranchMatch(found=reached[0])
    return BranchMatch(candidates=tuple(reached))
