"""Engine, sessionmaker, and the session scope (roadmap A2).

Everything above this file already speaks SQLAlchemy — the ORM models, the message repository, the
conversation repository — but nothing had ever *connected*. Sessions were constructed by tests, one
per test, against in-memory SQLite. This module is the missing half: it turns ``DATABASE_URL`` into
an :class:`~sqlalchemy.Engine` and hands out sessions with a defined lifetime, so the composition
root (A4) has a repository it can give to ``create_app``.

Two things here are decisions rather than plumbing, and both are about the connection *path* rather
than the database:

**The transaction pooler.** Supabase's application URI (port 6543) is pgbouncer in transaction
mode: a client connection is bound to a server connection only for the duration of a transaction,
and the next transaction may land on a different server connection. Server-side prepared statements
do not survive that — psycopg 3 prepares a statement on one backend and looks for it by name on
another, which fails as ``prepared statement "_pg3_0" already exists`` or ``does not exist``, under
load, intermittently, in production. :data:`POOL_MODE_TRANSACTION` is therefore the default:
``NullPool`` so SQLAlchemy holds no connection between checkouts, and ``prepare_threshold=None`` so
psycopg never prepares. ``POOL_MODE_SESSION`` is the opt-out for a direct connection (port 5432),
where ordinary pooling is both safe and faster. The default is the safe-but-slower one on purpose:
choosing wrong in this direction costs latency, and choosing wrong in the other costs an outage
that only appears under concurrency.

**The driver.** Supabase hands out ``postgresql://…``, which SQLAlchemy resolves to psycopg 2 —
a driver this project does not ship. :func:`normalize_database_url` rewrites a bare Postgres scheme
onto psycopg 3 so that pasting the URI from the dashboard works, while an explicitly named driver
(``postgresql+asyncpg://``) is left exactly as written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from apps.api.core.config import Settings

#: A callable that yields a session for the duration of a ``with`` block. ``Database.session`` is
#: the production one; a test passes its own. Everything that touches the database takes one of
#: these rather than a :class:`~sqlalchemy.orm.Session`, so nothing has to decide how long a
#: session lives except the thing that opened it.
SessionScope = Callable[[], AbstractContextManager[Session]]

PoolMode = Literal["transaction", "session"]

#: Behind pgbouncer in transaction mode (Supabase's port-6543 URI). The safe default.
POOL_MODE_TRANSACTION: PoolMode = "transaction"

#: Directly against Postgres, or through a session-mode pooler: ordinary connection pooling.
POOL_MODE_SESSION: PoolMode = "session"

#: The Postgres driver this project ships. A bare ``postgresql://`` URI is rewritten onto it.
DEFAULT_POSTGRES_DRIVER = "psycopg"

_POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})

#: Recycle pooled connections before a proxy or Postgres itself decides to. Only meaningful in
#: session mode; ``NullPool`` opens a fresh connection every time and has nothing to recycle.
_POOL_RECYCLE_SECONDS = 1800


def normalize_database_url(url: str) -> str:
    """Point a bare Postgres URI at the driver we actually ship; leave everything else alone.

    ``postgres://`` (the legacy spelling several providers still emit) and ``postgresql://`` both
    resolve to psycopg 2 in SQLAlchemy, which is not a dependency here. An explicit
    ``postgresql+driver://`` is a deliberate statement by whoever wrote it and is preserved.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    name, plus, driver = scheme.partition("+")
    if name.lower() not in _POSTGRES_SCHEMES:
        return url
    if plus:  # a driver was named explicitly; only the legacy `postgres` scheme needs fixing
        return f"postgresql+{driver}://{rest}"
    return f"postgresql+{DEFAULT_POSTGRES_DRIVER}://{rest}"


def engine_arguments(
    url: str, pool_mode: PoolMode = POOL_MODE_TRANSACTION
) -> tuple[str, dict[str, Any]]:
    """``(url, create_engine kwargs)`` for one ``DATABASE_URL``.

    Split out from :func:`create_db_engine` because the policy is the interesting part and the
    policy is testable without a database, a driver, or a network — which is what lets the
    pgbouncer rules above be pinned by a test in CI, where no Postgres driver is installed.
    """
    dsn = normalize_database_url(url)
    scheme = dsn.partition("://")[0].lower()
    name, _plus, driver = scheme.partition("+")
    if name not in _POSTGRES_SCHEMES:
        return dsn, {}  # SQLite in tests; its defaults are right and NullPool would break :memory:

    if pool_mode == POOL_MODE_SESSION:
        return dsn, {"pool_pre_ping": True, "pool_recycle": _POOL_RECYCLE_SECONDS}

    options: dict[str, Any] = {"poolclass": NullPool}
    if driver == DEFAULT_POSTGRES_DRIVER:
        # psycopg 3 prepares a statement after the fifth execution by default. Under a
        # transaction pooler the sixth execution may be on a different backend, which has never
        # heard of it. `None` disables preparation entirely; psycopg 2 has no equivalent because
        # it never prepares in the first place.
        options["connect_args"] = {"prepare_threshold": None}
    return dsn, options


def create_db_engine(url: str, pool_mode: PoolMode = POOL_MODE_TRANSACTION) -> Engine:
    """An engine for ``url`` under the pooling policy above."""
    dsn, options = engine_arguments(url, pool_mode)
    return create_engine(dsn, **options)


@dataclass(frozen=True, slots=True)
class Database:
    """An engine and its sessionmaker, plus the one blessed way to get a session out of them."""

    engine: Engine
    session_factory: sessionmaker[Session]

    @classmethod
    def from_engine(cls, engine: Engine) -> Database:
        """Wrap an existing engine — the seam tests use to run the real wiring on SQLite.

        ``expire_on_commit=False`` because the repositories commit and then the caller still
        wants the object it just wrote. The alternative is a re-SELECT per attribute access after
        every commit, on a connection that (in transaction mode) has already been handed back.
        """
        return cls(engine=engine, session_factory=sessionmaker(engine, expire_on_commit=False))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A session for one unit of work: commit on success, roll back on anything else.

        The rollback matters more than the commit. Without it a failed write leaves the session's
        transaction open, and with ``NullPool`` that transaction is holding a pgbouncer server
        connection until the socket closes.
        """
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_session(self) -> Iterator[Session]:
        """The same scope in FastAPI-dependency form: ``Depends(database.get_session)``."""
        with self.session() as session:
            yield session

    def dispose(self) -> None:
        """Close pooled connections. Called on application shutdown by the composition root."""
        self.engine.dispose()


def build_database(settings: Settings) -> Database:
    """The process's database, from configuration. Raises ``ConfigError`` without ``DATABASE_URL``.

    This is the point of use A1 talks about: ``DATABASE_URL`` is optional on ``Settings`` so the
    eval runner and the tests can construct settings without one, and required *here*, where a
    connection is actually about to be needed.
    """
    return Database.from_engine(create_db_engine(settings.database_dsn(), settings.pool_mode()))
