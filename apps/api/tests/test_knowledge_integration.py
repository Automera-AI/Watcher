"""End to end: real property data through the real DB, the real tool, the real receptionist.

``fixtures/demo_property_facts.json`` is a committed, curated export of one real property from
the operator's own property-management sheet, produced by ``scripts/import_property_facts.py``.
The unit tests (``test_knowledge.py``, ``test_receptionist.py``) cover the matching logic and the
dispatch in isolation with small fakes; this file is the one place the whole path — a JSON export,
persisted as ``FactRow`` rows behind RLS, read back by ``SqlAlchemyFactRepository``, matched by
``core.knowledge.best_match``, and answered by the receptionist — runs together, on data that
looks like what a real tenant would actually load.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.api.conversations.receptionist import handle
from apps.api.conversations.task import TaskStatus
from apps.api.conversations.tools import REGISTRY, AnswerFromKnowledge
from apps.api.db.engine import Database
from apps.api.db.knowledge_repo import SqlAlchemyFactRepository
from apps.api.db.models import FactRow
from apps.api.schemas.envelope import InboundTurn

FIXTURE = Path(__file__).parent / "fixtures" / "demo_property_facts.json"


def _turn(text: str, tenant_id: uuid.UUID) -> InboundTurn:
    return InboundTurn(
        tenant_id=tenant_id,
        channel="whatsapp",
        channel_thread_id="thread-1",
        channel_identity="+966500000000",
        modality="text",
        text=text,
        received_at=datetime.now(UTC),
        idempotency_key=f"key-{text}",
    )


@pytest.fixture
def wired_knowledge(database: Database, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    """A real tenant, with the fixture's facts persisted through RLS-scoped sessions."""
    tenant_id = uuid.uuid4()
    facts = json.loads(FIXTURE.read_text())
    assert facts, "fixture is empty — nothing to test against"

    with database.tenant_session(str(tenant_id)) as session:
        for fact in facts:
            session.add(FactRow(tenant_id=tenant_id, **fact))

    monkeypatch.setitem(
        REGISTRY,
        "answer_from_knowledge",
        AnswerFromKnowledge(SqlAlchemyFactRepository(database.tenant_session)),
    )
    return tenant_id


def test_a_public_question_is_answered_from_real_property_data(
    wired_knowledge: uuid.UUID,
) -> None:
    action, task = asyncio.run(
        handle(
            _turn("how many bedrooms are there", wired_knowledge),
            "property_question",
            0.95,
            {},
            None,
        )
    )
    assert action.kind == "say"
    assert action.text == "5"
    assert task.status == TaskStatus.COMPLETED


def test_a_paraphrased_question_still_matches(wired_knowledge: uuid.UUID) -> None:
    action, _task = asyncio.run(
        handle(
            _turn("is there somewhere to park nearby?", wired_knowledge),
            "property_question",
            0.95,
            {},
            None,
        )
    )
    assert action.kind == "say"
    assert "permit holders" in action.text


def test_the_wifi_password_is_withheld_from_an_unverified_guest(
    wired_knowledge: uuid.UUID,
) -> None:
    action, task = asyncio.run(
        handle(
            _turn("what's the wifi password", wired_knowledge),
            "property_question",
            0.95,
            {},
            None,
            identity_verified=False,
        )
    )
    assert action.kind == "handoff"
    assert task.status == TaskStatus.HANDED_OFF


def test_the_wifi_password_is_given_to_a_verified_guest(wired_knowledge: uuid.UUID) -> None:
    action, _task = asyncio.run(
        handle(
            _turn("what's the wifi password", wired_knowledge),
            "property_question",
            0.95,
            {},
            None,
            identity_verified=True,
        )
    )
    assert action.kind == "say"
    assert action.text == "48848NymiAgjiJ"


def test_the_wifi_network_name_needs_no_verification(wired_knowledge: uuid.UUID) -> None:
    """Splitting the source cell into two facts (the import script) pays off here: the network
    name is not behind the same gate as the password it shared a spreadsheet cell with."""
    action, _task = asyncio.run(
        handle(
            _turn("what's the wifi network called", wired_knowledge),
            "property_question",
            0.95,
            {},
            None,
            identity_verified=False,
        )
    )
    assert action.kind == "say"
    assert action.text == "Hyperoptic Fibre 5AF3"


def test_an_unanswerable_question_fetches_a_person_rather_than_guessing(
    wired_knowledge: uuid.UUID,
) -> None:
    action, task = asyncio.run(
        handle(
            _turn("do you have a rooftop pool", wired_knowledge),
            "property_question",
            0.95,
            {},
            None,
        )
    )
    assert action.kind == "handoff"
    assert task.status == TaskStatus.HANDED_OFF


def test_a_different_tenant_sees_none_of_these_facts(database: Database) -> None:
    """RLS, exercised through the real repository rather than asserted about it in the abstract."""
    other_tenant = uuid.uuid4()
    repo = SqlAlchemyFactRepository(database.tenant_session)
    assert repo.search(str(other_tenant)) == []
