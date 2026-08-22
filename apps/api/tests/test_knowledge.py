"""Tests for the knowledge-base matching logic and its repository (roadmap 2.4)."""

from __future__ import annotations

import uuid

from apps.api.core.knowledge import Fact, best_match
from apps.api.db.engine import Database
from apps.api.db.knowledge_repo import SqlAlchemyFactRepository
from apps.api.db.models import FactRow


def _fact(question: str, answer: str = "answer", *, sensitive: bool = False) -> Fact:
    return Fact(id="1", topic="topic", question=question, answer=answer, sensitive=sensitive)


def test_no_facts_never_matches() -> None:
    assert best_match("is there parking?", []) is None


def test_an_exact_question_matches() -> None:
    fact = _fact("is there parking?")
    assert best_match("is there parking?", [fact]) is fact


def test_a_close_paraphrase_still_matches() -> None:
    fact = _fact("is there parking available at the property")
    assert best_match("is there parking?", [fact]) is fact


def test_an_unrelated_question_does_not_match() -> None:
    fact = _fact("what time is checkout")
    assert best_match("is there a pool?", [fact]) is None


def test_blank_text_never_matches() -> None:
    fact = _fact("is there parking?")
    assert best_match("   ", [fact]) is None


def test_the_best_of_several_candidates_wins() -> None:
    parking = _fact("is there parking?", answer="Yes, in the basement.")
    pool = _fact("is there a pool?", answer="No pool on site.")
    assert best_match("is there parking available?", [pool, parking]) is parking


class TestSqlAlchemyFactRepository:
    def test_returns_a_tenants_active_facts(self, database: Database) -> None:
        tenant_id = uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="wifi",
                    question="what's the wifi password",
                    answer="Flex2026",
                    sensitive=False,
                )
            )
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="wifi",
                    question="what's the network name",
                    answer="Flex_5B",
                    sensitive=False,
                    active=False,
                )
            )

        repo = SqlAlchemyFactRepository(database.tenant_session)
        facts = repo.search(str(tenant_id))

        assert [f.question for f in facts] == ["what's the wifi password"]
        assert facts[0].answer == "Flex2026"
        assert facts[0].sensitive is False

    def test_a_tenant_sees_no_facts_from_another_tenant(self, database: Database) -> None:
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        with database.tenant_session(str(theirs)) as session:
            session.add(
                FactRow(tenant_id=theirs, topic="wifi", question="wifi password", answer="x")
            )

        repo = SqlAlchemyFactRepository(database.tenant_session)
        assert repo.search(str(mine)) == []
