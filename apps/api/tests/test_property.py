"""Tests for property resolution and per-property fact scoping (roadmap 2.8)."""

from __future__ import annotations

import asyncio
import uuid

from apps.api.conversations.tools import AnswerFromKnowledge
from apps.api.core.property import Property, resolve_property
from apps.api.db.engine import Database
from apps.api.db.knowledge_repo import SqlAlchemyFactRepository
from apps.api.db.models import FactRow
from apps.api.db.models import Property as PropertyRow
from apps.api.db.property_repo import SqlAlchemyPropertyRepository


def _prop(name: str, *, external_id: str | None = None) -> Property:
    return Property(id=str(uuid.uuid4()), name=name, external_id=external_id)


class TestResolvePropertyPolicy:
    def test_no_properties_resolves_to_none(self) -> None:
        assert resolve_property([]) is None

    def test_one_property_is_always_the_answer(self) -> None:
        only = _prop("Marina 2-bed")
        assert resolve_property([only]) == only.id

    def test_many_properties_without_a_hint_resolve_to_none(self) -> None:
        assert resolve_property([_prop("A"), _prop("B")]) is None

    def test_a_hint_matching_an_id_wins_over_the_single_property_fallback(self) -> None:
        a, b = _prop("A"), _prop("B")
        assert resolve_property([a, b], hint=b.id) == b.id

    def test_a_hint_matching_an_external_id_resolves_to_the_row_id(self) -> None:
        a = _prop("A", external_id="PMS-1")
        b = _prop("B", external_id="PMS-2")
        assert resolve_property([a, b], hint="PMS-2") == b.id

    def test_a_hint_that_matches_nothing_resolves_to_none_rather_than_guessing(self) -> None:
        # Even with a single property, an explicit hint that names none is not overridden by the
        # fallback: a misconfigured endpoint degrades to tenant-wide facts, it does not answer for
        # whatever unit happens to be the only one.
        only = _prop("A")
        assert resolve_property([only], hint="PMS-unknown") is None


class TestSqlAlchemyPropertyRepository:
    def test_lists_only_active_properties(self, database: Database) -> None:
        tenant_id = uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            session.add(PropertyRow(tenant_id=tenant_id, name="Live unit", active=True))
            session.add(PropertyRow(tenant_id=tenant_id, name="Delisted unit", active=False))

        repo = SqlAlchemyPropertyRepository(database.tenant_session)
        names = [p.name for p in repo.list_active(str(tenant_id))]
        assert names == ["Live unit"]

    def test_resolves_a_single_property_tenant_to_its_one_unit(self, database: Database) -> None:
        tenant_id = uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            row = PropertyRow(tenant_id=tenant_id, name="The only unit")
            session.add(row)
        repo = SqlAlchemyPropertyRepository(database.tenant_session)
        resolved = repo.resolve(str(tenant_id))
        assert resolved is not None
        assert repo.list_active(str(tenant_id))[0].id == resolved

    def test_a_tenant_sees_no_properties_from_another_tenant(self, database: Database) -> None:
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        with database.tenant_session(str(theirs)) as session:
            session.add(PropertyRow(tenant_id=theirs, name="Not yours"))
        repo = SqlAlchemyPropertyRepository(database.tenant_session)
        assert repo.list_active(str(mine)) == []
        assert repo.resolve(str(mine)) is None


class TestPropertyScopedFactSearch:
    def test_a_resolved_property_sees_its_own_facts_and_the_tenant_wide_ones(
        self, database: Database
    ) -> None:
        tenant_id = uuid.uuid4()
        marina = uuid.uuid4()
        downtown = uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            session.add(PropertyRow(id=marina, tenant_id=tenant_id, name="Marina"))
            session.add(PropertyRow(id=downtown, tenant_id=tenant_id, name="Downtown"))
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="parking",
                    question="is there parking",
                    answer="Marina: east side",
                    property_id=marina,
                )
            )
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="parking",
                    question="is there parking",
                    answer="Downtown: valet only",
                    property_id=downtown,
                )
            )
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="office",
                    question="what are the office hours",
                    answer="9 to 5",  # tenant-wide (property_id is NULL)
                )
            )

        repo = SqlAlchemyFactRepository(database.tenant_session)
        answers = {f.answer for f in repo.search(str(tenant_id), str(marina))}
        assert answers == {"Marina: east side", "9 to 5"}

    def test_no_resolved_property_sees_only_tenant_wide_facts(self, database: Database) -> None:
        tenant_id = uuid.uuid4()
        unit = uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            session.add(PropertyRow(id=unit, tenant_id=tenant_id, name="A unit"))
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="parking",
                    question="is there parking",
                    answer="unit-specific",
                    property_id=unit,
                )
            )
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="office",
                    question="what are the office hours",
                    answer="tenant-wide",
                )
            )

        repo = SqlAlchemyFactRepository(database.tenant_session)
        answers = {f.answer for f in repo.search(str(tenant_id), None)}
        assert answers == {"tenant-wide"}


class TestAnswerFromKnowledgeResolvesProperty:
    """The tool, wired with a real resolver, answers from the resolved property's facts."""

    def test_a_single_property_tenants_message_is_scoped_to_that_property(
        self, database: Database
    ) -> None:
        tenant_id = uuid.uuid4()
        marina = uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            session.add(PropertyRow(id=marina, tenant_id=tenant_id, name="Marina"))
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="parking",
                    question="is there parking",
                    answer="Yes, on the east side.",
                    property_id=marina,
                )
            )

        tool = AnswerFromKnowledge(
            SqlAlchemyFactRepository(database.tenant_session),
            SqlAlchemyPropertyRepository(database.tenant_session),
        )
        result = asyncio.run(tool.run(tenant_id=str(tenant_id), question="is there parking?"))
        assert result.ok is True
        assert result.human_summary == "Yes, on the east side."

    def test_a_property_specific_fact_is_withheld_when_no_property_resolves(
        self, database: Database
    ) -> None:
        # Two units, no hint: nothing resolves, so a fact scoped to one unit must not leak to a
        # guest whose property we could not determine.
        tenant_id = uuid.uuid4()
        marina, downtown = uuid.uuid4(), uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            session.add(PropertyRow(id=marina, tenant_id=tenant_id, name="Marina"))
            session.add(PropertyRow(id=downtown, tenant_id=tenant_id, name="Downtown"))
            session.add(
                FactRow(
                    tenant_id=tenant_id,
                    topic="parking",
                    question="is there parking",
                    answer="Marina: east side",
                    property_id=marina,
                )
            )

        tool = AnswerFromKnowledge(
            SqlAlchemyFactRepository(database.tenant_session),
            SqlAlchemyPropertyRepository(database.tenant_session),
        )
        result = asyncio.run(tool.run(tenant_id=str(tenant_id), question="is there parking?"))
        assert result.ok is False

    def test_a_hint_selects_the_right_property_among_many(self, database: Database) -> None:
        tenant_id = uuid.uuid4()
        marina, downtown = uuid.uuid4(), uuid.uuid4()
        with database.tenant_session(str(tenant_id)) as session:
            session.add(
                PropertyRow(id=marina, tenant_id=tenant_id, name="Marina", external_id="PMS-M")
            )
            session.add(
                PropertyRow(id=downtown, tenant_id=tenant_id, name="Downtown", external_id="PMS-D")
            )
            for pid, answer in ((marina, "Marina: east side"), (downtown, "Downtown: valet only")):
                session.add(
                    FactRow(
                        tenant_id=tenant_id,
                        topic="parking",
                        question="is there parking",
                        answer=answer,
                        property_id=pid,
                    )
                )

        tool = AnswerFromKnowledge(
            SqlAlchemyFactRepository(database.tenant_session),
            SqlAlchemyPropertyRepository(database.tenant_session),
        )
        result = asyncio.run(
            tool.run(tenant_id=str(tenant_id), question="is there parking?", property_hint="PMS-D")
        )
        assert result.ok is True
        assert result.human_summary == "Downtown: valet only"
