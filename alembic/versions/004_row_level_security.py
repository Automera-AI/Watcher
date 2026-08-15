"""row-level security

Roadmap item B2. AGENTS.md calls tenant isolation non-negotiable and the addendum (§3) names the
mechanism — "row-level isolation by ``tenant_id`` via Postgres Row-Level Security plus an
application-layer filter (defense in depth)... each request sets a session GUC" — and until this
migration only the second half of that existed. Every query carried ``WHERE tenant_id = :tenant``
and nothing in the database would have objected to one that did not.

Four things this does, and the order matters:

1. **A role for the application.** ``watcher_app``, created ``NOLOGIN`` and without a password —
   ownership of the credential belongs to whoever deploys, not to a file in the repository, so the
   deploy runs ``ALTER ROLE watcher_app WITH LOGIN PASSWORD '…'`` once and ``DATABASE_URL`` names
   it. This is not decoration. On Supabase the ``postgres`` role that migrations run as has
   ``rolbypassrls = true``, so an application connecting with the URI from the dashboard is exempt
   from every policy below and RLS becomes an expensive no-op that reads as protection. The role
   also has no ``BYPASSRLS`` of its own and does not own the tables, which are the two other ways
   to be exempt by accident.

2. **RLS enabled *and forced* on every table.** ``FORCE`` covers the self-hosted tier, where the
   application may well own its own schema; an owner is otherwise exempt from its own policies.
   Tables with no tenant column (``eval_runs``, ``alembic_version``) get RLS with no policy for
   ``watcher_app`` at all, which closes them to it — and, more to the point, closes every table in
   ``public`` to Supabase's ``anon`` role, which PostgREST exposes to anyone holding the publishable
   key. The grants to ``anon``/``authenticated`` are revoked as well; two independent reasons for
   the same table to be unreadable is the point of defense in depth.

3. **One policy per tenant-scoped table**, all of the same shape: rows are visible when their
   ``tenant_id`` equals ``app.current_tenant()``, and writes must carry the same value. The helper
   returns ``NULL`` when the setting is absent, and ``tenant_id = NULL`` is never true, so a session
   that forgets to say who it is sees an empty database rather than everyone's.

4. **The one exception, deliberately narrow.** ``channel_configs`` answers "which tenant owns this
   endpoint", which is asked before a tenant is known and cannot be tenant-scoped without a
   chicken-and-egg. It gets a second, ``SELECT``-only policy that applies *only* when no tenant is
   set. A session that has adopted a tenant therefore sees that tenant's endpoints and no others;
   a session that has not sees endpoint rows and nothing else in the database.

Postgres-only: SQLite has no RLS, and the test suite's in-memory database would fail on the first
statement. The tenant filter in the queries is what isolates there, which is exactly the
application-layer half the addendum asks for in both tiers.

Revision ID: 004_row_level_security
Revises: 003_conversations
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004_row_level_security"
down_revision: str | None = "003_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The role the application connects as. Named here and in the deploy runbook, nowhere else.
APP_ROLE = "watcher_app"

#: The session-local setting the policies read. Mirrors ``db.engine.TENANT_SETTING``, which is what
#: sets it; the two names are the contract between the application and the database.
TENANT_SETTING = "app.current_tenant"

#: Tables whose rows belong to one tenant, identified by a ``tenant_id`` column.
TENANT_TABLES = (
    "audit_log",
    "channel_configs",
    "classifications",
    "contacts",
    "conversations",
    "corrections",
    "crm_cache",
    "destinations",
    "identity_resolutions",
    "inbox_items",
    "messages",
    "rules",
    "sources",
    "task_rows",
    "turns",
    "understandings",
    "usage_events",
)

#: ``tenants`` is the tenant, so its own primary key is what a policy compares against.
#: ``eval_runs`` and ``alembic_version`` belong to no tenant and get no policy: RLS on, nothing
#: permitted, which is the correct answer for a table the application has no business reading.
SELF_SCOPED_TABLE = "tenants"
UNSCOPED_TABLES = ("eval_runs", "alembic_version")

ALL_TABLES = (*TENANT_TABLES, SELF_SCOPED_TABLE, *UNSCOPED_TABLES)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return

    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOBYPASSRLS;
            END IF;
        END
        $$;
    """)

    # A schema of our own for the helper: `public` is where PostgREST looks, and a function that
    # reports the current tenant is not something to publish on an HTTP API.
    op.execute("CREATE SCHEMA IF NOT EXISTS app;")
    op.execute(f"""
        CREATE OR REPLACE FUNCTION app.current_tenant() RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid
        $$;
    """)
    # `current_setting(..., true)` returns NULL rather than raising when unset, and NULLIF turns the
    # empty string into NULL too — a session that sets the value to '' is saying "no tenant", not
    # asking for a cast error halfway through a query.

    op.execute(f"GRANT USAGE ON SCHEMA public, app TO {APP_ROLE};")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.current_tenant() TO {APP_ROLE};")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};"
    )
    # `usage_events` has a BIGSERIAL key, and inserting into it needs the sequence.
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};"
    )

    for table in ALL_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        # Supabase grants the PostgREST roles access to everything in `public` by default. Nothing
        # in this product is meant to be reached with a publishable key.
        op.execute(f"REVOKE ALL ON {table} FROM anon, authenticated;")

    for table in TENANT_TABLES:
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                FOR ALL TO {APP_ROLE}
                USING (tenant_id = app.current_tenant())
                WITH CHECK (tenant_id = app.current_tenant());
        """)

    op.execute(f"""
        CREATE POLICY tenant_isolation ON {SELF_SCOPED_TABLE}
            FOR ALL TO {APP_ROLE}
            USING (id = app.current_tenant())
            WITH CHECK (id = app.current_tenant());
    """)

    # The bootstrap lookup: see the module docstring, and `db/tenant_resolver.py` for the caller.
    op.execute(f"""
        CREATE POLICY endpoint_lookup ON channel_configs
            FOR SELECT TO {APP_ROLE}
            USING (app.current_tenant() IS NULL);
    """)


def downgrade() -> None:
    if not _is_postgres():
        return

    op.execute("DROP POLICY IF EXISTS endpoint_lookup ON channel_configs;")
    for table in (*TENANT_TABLES, SELF_SCOPED_TABLE):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
    for table in ALL_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP FUNCTION IF EXISTS app.current_tenant();")
    # The role and its grants outlive the migration on purpose: dropping a role that a running
    # deployment is connected as turns a schema rollback into an outage. Removing it is a
    # deliberate operational act, not a side effect of `alembic downgrade`.
