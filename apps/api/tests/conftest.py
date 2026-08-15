"""Fixtures shared by the tests that need a real database rather than a double.

SQLite, in memory, with a schema created from the same ORM metadata Postgres gets. ``StaticPool``
plus ``check_same_thread=False`` because the composition root classifies on a worker thread: the
default SQLite pool would hand that thread its own connection, and an in-memory database *is* its
connection, so the worker would find an empty schema and the test would prove nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.db.base import Base
from apps.api.db.engine import Database


@pytest.fixture
def database() -> Database:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return Database.from_engine(engine)
