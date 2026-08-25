"""facts

Roadmap item 2.4. The knowledge base: zero tables before this. ``facts`` follows migration 004's
row-level-security shape exactly — a table this migration creates gets the same treatment 004 gave
every table that existed at the time, so a tenant's facts are as isolated as its messages are.

``ALTER DEFAULT PRIVILEGES`` (004) already covers ``GRANT``s to ``watcher_app`` for any table
created afterwards; what it does not cover is enabling RLS, which is per-table and has no
"default" equivalent. This migration does that explicitly, the same four steps 004 documents:
enable and force RLS, revoke the PostgREST roles, add the tenant-isolation policy.

Revision ID: 005_facts
Revises: 004_row_level_security
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005_facts"
down_revision: str | None = "004_row_level_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Matches migration 004's ``APP_ROLE`` / ``TENANT_SETTING``. Not imported from there — Alembic
#: migrations are meant to stand alone, so a later edit to 004's constants cannot silently change
#: what an already-applied migration did.
APP_ROLE = "watcher_app"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "facts",
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_facts_tenant_id"), "facts", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_facts_topic"), "facts", ["topic"], unique=False)

    if not _is_postgres():
        return

    op.execute("ALTER TABLE facts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE facts FORCE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON facts FROM anon, authenticated;")
    # Static DDL: APP_ROLE is a fixed module constant, never runtime or user input.
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    op.execute(f"""
        CREATE POLICY tenant_isolation ON facts
            FOR ALL TO {APP_ROLE}
            USING (tenant_id = app.current_tenant())
            WITH CHECK (tenant_id = app.current_tenant());
    """)


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON facts;")
        op.execute("ALTER TABLE facts NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE facts DISABLE ROW LEVEL SECURITY;")

    op.drop_index(op.f("ix_facts_topic"), table_name="facts")
    op.drop_index(op.f("ix_facts_tenant_id"), table_name="facts")
    op.drop_table("facts")
