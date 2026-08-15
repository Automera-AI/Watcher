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

**Which tenant a session is acting as (B2).** Row-Level Security is enforced in Postgres against
one session-local setting, ``app.current_tenant``. Migration ``004`` writes the policies; this file
is where the setting is *set*, because a session is the only thing that can carry it. Hence two
scopes rather than one:

* :data:`TenantScope` — :meth:`Database.tenant_session`, which stamps the tenant on the transaction
  before yielding. Every adapter that touches tenant data takes one of these, so a session cannot
  be opened for that data without saying whose it is. The tenant was always available at those call
  sites (every port already takes ``tenant_id``); what was missing was anywhere to put it.
* :data:`SessionScope` — the plain, unstamped scope. Exactly one thing still uses it: the tenant
  resolver, which reads ``channel_configs`` to answer *which* tenant an endpoint belongs to and so
  cannot already know. Migration ``004`` gives that one lookup a policy of its own; the resolver's
  session can therefore see endpoint rows and no guest data of any kind, because every other policy
  fails closed when the setting is absent.

Setting it is a no-op on SQLite, which has no such concept — the tests would otherwise all fail on
a statement their database cannot parse, and the alternative (a Postgres-only test suite) is worse.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from apps.api.core.config import Settings

#: A callable that yields a session for the duration of a ``with`` block. ``Database.session`` is
#: the production one; a test passes its own. Everything that touches the database takes one of
#: these rather than a :class:`~sqlalchemy.orm.Session`, so nothing has to decide how long a
#: session lives except the thing that opened it.
SessionScope = Callable[[], AbstractContextManager[Session]]

#: The same, for a session that acts as one tenant: ``Database.tenant_session``. Everything that
#: reads or writes tenant data takes one of these instead of a :data:`SessionScope`, which is what
#: makes "which tenant is this?" unanswerable-by-omission rather than merely conventional.
TenantScope = Callable[[str], AbstractContextManager[Session]]

#: The session-local setting RLS policies read (addendum §3). Named in migration ``004`` too;
#: changing it means changing both, which is why it is a constant in one of them.
TENANT_SETTING = "app.current_tenant"

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


def set_current_tenant(session: Session, tenant_id: str) -> None:
    """Stamp ``tenant_id`` on this session's transaction, for RLS to enforce against.

    ``set_config(..., is_local => true)`` rather than ``SET``: the setting lasts exactly as long as
    the transaction, so a connection handed back to the pooler cannot carry one tenant's identity
    into the next tenant's transaction. It is also the parameterised form — ``SET`` takes a literal,
    and a literal built by interpolation is an injection waiting for a tenant id that came from
    somewhere unexpected.

    Executing it is what opens the transaction, which is why it happens before anything else the
    session does. On SQLite there is nothing to set and nothing enforcing it, so this returns
    quietly; the tenant filter every query already carries is what isolates the tests.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config(:name, :value, true)"), {"name": TENANT_SETTING, "value": tenant_id}
    )


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

    @contextmanager
    def tenant_session(self, tenant_id: str) -> Iterator[Session]:
        """A session acting as one tenant: the same unit of work, with RLS in force.

        The stamp is applied inside the scope, so a rollback takes the setting with it and there is
        no window in which the transaction exists without an owner.
        """
        with self.session() as session:
            set_current_tenant(session, tenant_id)
            yield session

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
