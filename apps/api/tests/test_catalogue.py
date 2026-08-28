"""Matching a patient's words against the catalogue, and reading a short reply (demo step 6).

The two pure pieces the booking journey rests on. Both are tested here rather than only through a
conversation because their interesting cases are the ones a conversation makes hard to see: a
service that matches two catalogue rows with the same price, and a word that means yes in one
position and goodbye in another.
"""

from __future__ import annotations

import pytest

from apps.api.clinic.catalogue import resolve_branch, resolve_service
from apps.api.conversations.confirmation import reads_as_no, reads_as_yes
from apps.api.core.clinic import Branch, Service

BASIC_FACIAL = Service(
    code="DT001",
    name="Basic Facial",
    price_minor=75_000,
    duration_minutes=45,
    aliases=("فاشيال بيسك", "el facial el basic"),
)
FACIAL = Service(code="DT002", name="Facial", price_minor=75_000, duration_minutes=45)
FULL_BODY = Service(
    code="DT020",
    name="Laser Full Body 12 Sessions",
    price_minor=1_635_000,
    duration_minutes=60,
    session_count=12,
)
LEGS = Service(
    code="DT021",
    name="Laser Legs 12 Sessions",
    price_minor=1_635_000,
    duration_minutes=60,
    session_count=12,
)
CATALOGUE = (BASIC_FACIAL, FACIAL, FULL_BODY, LEGS)

MAADI = Branch(external_id="DC01", name="Maadi", area="Maadi, Cairo", aliases=("المعادي",))
NEW_CAIRO = Branch(external_id="DC02", name="New Cairo", area="Fifth Settlement")
BRANCHES = (MAADI, NEW_CAIRO)


class TestServices:
    @pytest.mark.parametrize(
        ("written", "code"),
        [
            ("Basic Facial", "DT001"),
            ("basic facial", "DT001"),
            ("فاشيال بيسك", "DT001"),
            ("el facial el basic", "DT001"),
            ("DT001", "DT001"),
            ("Facial", "DT002"),
            ("laser legs", "DT021"),
        ],
    )
    def test_a_name_an_alias_or_a_code_all_reach_the_same_row(
        self, written: str, code: str
    ) -> None:
        found = resolve_service(written, CATALOGUE)
        assert found.found is not None and found.found.code == code

    def test_an_exact_name_wins_over_a_longer_one_that_contains_it(self) -> None:
        """ "Facial" is a service in its own right. It must not be dragged into a question by
        "Basic Facial" happening to contain the word."""
        found = resolve_service("facial", CATALOGUE)
        assert found.found is FACIAL

    def test_words_that_reach_two_services_come_back_as_two(self) -> None:
        """Both 12-session packages cost 16,350. Picking one is invisible and wrong."""
        found = resolve_service("laser 12", CATALOGUE)
        assert found.ambiguous
        assert {s.code for s in found.candidates} == {"DT020", "DT021"}
        assert found.found is None

    def test_a_service_the_catalogue_does_not_list_reaches_nothing(self) -> None:
        """Quoting is from the current catalogue or not at all. "Nothing" is a safe answer."""
        found = resolve_service("بوتوكس", CATALOGUE)
        assert found.missing

    def test_an_arabic_term_with_no_alias_behind_it_reaches_nothing(self) -> None:
        """Bilingual matching is data. Nothing here translates, and this is what that costs.

        The fix is a column in the clinic's own file — the Arabic a service answers to is
        something the client writes down, not a dictionary in this repository.
        """
        assert resolve_service("ليزر", CATALOGUE).missing

    @pytest.mark.parametrize("written", ["", "   ", "؟"])
    def test_nothing_useful_reaches_nothing(self, written: str) -> None:
        assert resolve_service(written, CATALOGUE).found is None

    def test_session_counts_are_never_treated_as_noise(self) -> None:
        """ "6 sessions" versus "12 sessions" is the whole difference between two prices."""
        found = resolve_service("laser full body 12 sessions", CATALOGUE)
        assert found.found is FULL_BODY


class TestBranches:
    @pytest.mark.parametrize(
        ("written", "external_id"),
        [
            ("Maadi", "DC01"),
            ("المعادي", "DC01"),
            ("فرع المعادي", "DC01"),
            ("DC01", "DC01"),
            ("Fifth Settlement", "DC02"),
        ],
    )
    def test_a_branch_answers_to_its_name_its_area_its_id_and_its_aliases(
        self, written: str, external_id: str
    ) -> None:
        found = resolve_branch(written, BRANCHES)
        assert found.found is not None and found.found.external_id == external_id

    def test_a_branch_nobody_has_reaches_nothing(self) -> None:
        assert resolve_branch("Alexandria", BRANCHES).missing

    def test_a_word_two_branches_share_is_a_question(self) -> None:
        cairo_branches = (
            Branch(external_id="DC03", name="Nasr City", area="Cairo"),
            Branch(external_id="DC04", name="Heliopolis", area="Cairo"),
        )
        found = resolve_branch("cairo", cairo_branches)
        assert found.ambiguous
        assert {b.external_id for b in found.candidates} == {"DC03", "DC04"}

    def test_an_empty_branch_reaches_nothing(self) -> None:
        assert resolve_branch("", BRANCHES).found is None


class TestShortReplies:
    @pytest.mark.parametrize(
        "written",
        ["أيوه", "ايوة", "تمام", "ماشي", "حاضر", "أيوه احجزيلي", "yes", "ok please", "aywa"],
    )
    def test_agreement_is_recognised(self, written: str) -> None:
        assert reads_as_yes(written)

    @pytest.mark.parametrize("written", ["لا", "لأ", "لا شكرا", "no", "no thanks", "mesh kda"])
    def test_refusal_is_recognised(self, written: str) -> None:
        assert reads_as_no(written)
        assert not reads_as_yes(written)

    def test_a_refusal_wearing_a_politeness_word_is_still_a_refusal(self) -> None:
        """A reply carrying both. Read as agreement it books an appointment nobody agreed to."""
        assert reads_as_no("لا تمام")
        assert not reads_as_yes("لا تمام")

    @pytest.mark.parametrize(
        "written",
        [
            "",
            "شكرا",
            "عايزة أغير الميعاد",
            "أيوه بس ممكن نغير الميعاد لبكرة",
            "ممكن أعرف السعر الأول",
        ],
    )
    def test_anything_that_is_not_a_bare_answer_is_neither(self, written: str) -> None:
        """A message carrying a new request goes to the flat vocabulary, not to the read-back."""
        assert not reads_as_yes(written)
        assert not reads_as_no(written)
