"""Fixtures shared by the tests that need a real database rather than a double.

SQLite, in memory, with a schema created from the same ORM metadata Postgres gets. ``StaticPool``
plus ``check_same_thread=False`` because the composition root classifies on a worker thread: the
default SQLite pool would hand that thread its own connection, and an in-memory database *is* its
connection, so the worker would find an empty schema and the test would prove nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.conversations.tools import REGISTRY
from apps.api.db.base import Base
from apps.api.db.engine import Database


@pytest.fixture
def database() -> Database:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return Database.from_engine(engine)


@pytest.fixture(autouse=True)
def _restore_tool_registry() -> Iterator[None]:
    """Undo whatever a test did to the process-global ``REGISTRY`` (``conversations/tools.py``).

    ``main.assemble`` calls ``configure_knowledge`` on every invocation, wiring
    ``answer_from_knowledge`` to a real ``SqlAlchemyFactRepository`` bound to *that test's*
    database. ``REGISTRY`` is a module-level dict — the same kind of shared process state
    ``apps/api/worker.py`` already warns about for ``get_settings()`` — so without this, a test
    that calls ``assemble`` (``test_main.py``) leaves a repository pointing at a disposed
    connection for the next test to trip over, whether or not that test knows the registry
    exists.
    """
    before = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(before)
