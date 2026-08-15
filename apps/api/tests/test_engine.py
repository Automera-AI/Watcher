"""Tests for the DB engine, the pooling policy, and the session scope (roadmap A2).

The pooling assertions are the point of this file. They run without a Postgres driver, without a
database and without a network, because the policy is a pure function of the URL and the mode —
which is what makes it possible to pin the pgbouncer rules in CI, where none of those exist.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from apps.api.channels import ConfigError
from apps.api.core.config import Settings
from apps.api.db.base import Base
from apps.api.db.engine import (
    POOL_MODE_SESSION,
    POOL_MODE_TRANSACTION,
    Database,
    build_database,
    create_db_engine,
    engine_arguments,
    normalize_database_url,
)
from apps.api.db.models import Tenant

SUPABASE_POOLER = "postgresql://postgres.abc:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"


# ── URL normalization ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (SUPABASE_POOLER, f"postgresql+psycopg://{SUPABASE_POOLER.partition('://')[2]}"),
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgres+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite://", "sqlite://"),
        ("sqlite+pysqlite:///./watcher.db", "sqlite+pysqlite:///./watcher.db"),
    ],
)
def test_normalize_database_url(given: str, expected: str) -> None:
    """A pasted Supabase URI reaches the driver we ship; a named driver is left alone."""
    assert normalize_database_url(given) == expected


def test_normalization_preserves_the_credentials_and_the_host() -> None:
    """The rewrite touches the scheme and nothing else — it is handling a password."""
    assert normalize_database_url(SUPABASE_POOLER).endswith(SUPABASE_POOLER.partition("://")[2])


# ── Pooling policy ─────────────────────────────────────────────────────────────────────────


def test_transaction_mode_disables_both_pools() -> None:
    """The Supabase default: no SQLAlchemy pool, no psycopg prepared statements.

    Both halves matter and they fail differently. Keeping the SQLAlchemy pool holds server
    connections pgbouncer wants to reassign; keeping prepared statements produces
    ``prepared statement "_pg3_0" already exists`` on whichever request happens to land on a
    backend that has not seen it — under load, intermittently, in production.
    """
    _dsn, options = engine_arguments(SUPABASE_POOLER, POOL_MODE_TRANSACTION)

    assert options["poolclass"] is NullPool
    assert options["connect_args"] == {"prepare_threshold": None}


def test_transaction_mode_is_the_default() -> None:
    """Wrong in this direction costs latency. Wrong the other way costs an outage."""
    assert engine_arguments(SUPABASE_POOLER) == engine_arguments(SUPABASE_POOLER, "transaction")


def test_psycopg2_gets_no_prepare_threshold() -> None:
    """psycopg 2 never prepares, and passing it an unknown connect argument is a TypeError."""
    _dsn, options = engine_arguments("postgresql+psycopg2://u:p@h/db", POOL_MODE_TRANSACTION)

    assert options["poolclass"] is NullPool
    assert "connect_args" not in options


def test_session_mode_pools_normally() -> None:
    """A direct connection is the case where pooling is both safe and worth having."""
    _dsn, options = engine_arguments("postgresql://u:p@h:5432/db", POOL_MODE_SESSION)

    assert "poolclass" not in options
    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] > 0


def test_sqlite_keeps_its_own_defaults() -> None:
    """NullPool against ``sqlite://`` drops the database between connections — it is the memory."""
    assert engine_arguments("sqlite://") == ("sqlite://", {})


# ── Database, sessions and configuration ───────────────────────────────────────────────────


def _tenant(name: str) -> Tenant:
    return Tenant(name=name, tier="saas")


def test_session_commits_on_success(database: Database) -> None:
    with database.session() as session:
        session.add(_tenant("Acme"))

    with database.session() as session:
        assert session.execute(select(Tenant.name)).scalars().all() == ["Acme"]


def test_session_rolls_back_on_failure(database: Database) -> None:
    """A half-written unit of work is never committed, and the error still reaches the caller."""
    with pytest.raises(RuntimeError, match="boom"):
        with database.session() as session:
            session.add(_tenant("Acme"))
            raise RuntimeError("boom")

    with database.session() as session:
        assert session.execute(select(Tenant.name)).scalars().all() == []


def test_session_is_closed_even_when_the_body_raises(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure path is the one that leaks. A session left open under ``NullPool`` is a
    pgbouncer server connection held by a transaction nobody is going to end."""
    closed: list[int] = []
    original = Session.close

    def spy(self: Session) -> None:
        closed.append(1)
        original(self)

    monkeypatch.setattr(Session, "close", spy)

    with pytest.raises(RuntimeError):
        with database.session() as session:
            session.add(_tenant("Acme"))
            raise RuntimeError("boom")

    assert closed


def test_get_session_yields_exactly_one_session(database: Database) -> None:
    """The FastAPI-dependency form: one session, then teardown."""
    sessions = list(database.get_session())
    assert len(sessions) == 1


def test_create_db_engine_applies_the_policy() -> None:
    engine = create_db_engine("sqlite://")
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar_one() == 1


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A process environment with none of our variables in it.

    The machine running these tests may export ``DATABASE_URL``; a test asserting that a missing
    one raises must not depend on whose laptop it is on.
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def test_build_database_requires_database_url(clean_env: pytest.MonkeyPatch) -> None:
    """``DATABASE_URL`` is optional to construct settings and required to open a connection."""
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        build_database(Settings(_env_file=None))


def test_build_database_uses_the_configured_url(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_URL", "sqlite://")
    database = build_database(Settings(_env_file=None))

    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add(_tenant("Acme"))
    with database.session() as session:
        assert session.execute(select(Tenant.name)).scalars().all() == ["Acme"]


def test_pool_mode_defaults_to_the_pooler_safe_one(clean_env: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).pool_mode() == POOL_MODE_TRANSACTION


def test_pool_mode_is_configurable(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DATABASE_POOL_MODE", "session")
    assert Settings(_env_file=None).pool_mode() == POOL_MODE_SESSION
