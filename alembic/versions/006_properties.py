"""properties

Roadmap item 2.8. A client is an agency with many units, not a single flat, so a knowledge-base
fact ("the wifi password is…", "parking is on the east side") belongs to one property, not all of
them. This migration adds the ``properties`` table and a nullable ``facts.property_id`` that scopes
a fact to one unit — ``NULL`` meaning tenant-wide, which is the common case and the reason the
column is nullable rather than required.

``properties`` follows migration 004/005's row-level-security shape exactly: a new tenant-scoped
table gets RLS enabled and forced, the PostgREST roles revoked, and the same tenant-isolation
policy every other tenant table carries. ``ALTER DEFAULT PRIVILEGES`` (004) already covers the
``GRANT``s to ``watcher_app`` for a table created afterwards; enabling RLS is per-table and has no
default, so it is done here explicitly.

The added ``facts.property_id`` inherits ``facts``'s existing RLS untouched — a column is not a new
table — and a foreign key to ``properties`` keeps a fact from pointing at a unit that does not
exist. Both sides are the same tenant's rows; RLS on each table enforces that independently.

Revision ID: 006_properties
Revises: 005_facts
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_properties"
down_revision: str | None = "005_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Matches migration 004's ``APP_ROLE``. Not imported from there — Alembic migrations stand alone.
APP_ROLE = "watcher_app"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "properties",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_properties_tenant_extid"),
    )
    op.create_index(op.f("ix_properties_tenant_id"), "properties", ["tenant_id"], unique=False)

    op.add_column("facts", sa.Column("property_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_facts_property_id"), "facts", ["property_id"], unique=False)

    if not _is_postgres():
        # SQLite has no ALTER for constraints, and the test schema is built from ORM metadata
        # (``Base.metadata.create_all``), which already carries the foreign key. So the FK and the
        # RLS below are Postgres-only, the same split migration 004/005 make for RLS.
        return

    # A fact must not point at a unit that does not exist; both sides are the same tenant's rows,
    # RLS on each table enforcing that independently.
    op.create_foreign_key("fk_facts_property_id", "facts", "properties", ["property_id"], ["id"])

    op.execute("ALTER TABLE properties ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE properties FORCE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON properties FROM anon, authenticated;")
    # Static DDL: APP_ROLE is a fixed module constant, never runtime or user input.
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    op.execute(f"""
        CREATE POLICY tenant_isolation ON properties
            FOR ALL TO {APP_ROLE}
            USING (tenant_id = app.current_tenant())
            WITH CHECK (tenant_id = app.current_tenant());
    """)


def downgrade() -> None:
    if _is_postgres():
        op.drop_constraint("fk_facts_property_id", "facts", type_="foreignkey")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON properties;")
        op.execute("ALTER TABLE properties NO FORCE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE properties DISABLE ROW LEVEL SECURITY;")

    op.drop_index(op.f("ix_facts_property_id"), table_name="facts")
    op.drop_column("facts", "property_id")

    op.drop_index(op.f("ix_properties_tenant_id"), table_name="properties")
    op.drop_table("properties")
