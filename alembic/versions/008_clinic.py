"""clinic branches, services, availability and bookings

Demo step 3. The clinic vertical's transactional core: the four tables ``check_availability``,
``quote_price``, ``hold_slot`` and ``confirm_booking`` will read and write. Until this migration the
vertical could greet, close, answer from the knowledge base and hand off, and nothing else — there
was nowhere for an appointment to exist.

Shape follows 004/005/006 exactly, because a new tenant-scoped table that does not is a table
outside the isolation guarantee: RLS enabled *and* forced, the PostgREST roles revoked, and the same
``tenant_isolation`` policy every other tenant table carries. ``ALTER DEFAULT PRIVILEGES`` (004)
already covers the ``GRANT``s to ``watcher_app`` for tables created afterwards; enabling RLS is
per-table and has no default, so it is done here explicitly.

Foreign keys are declared inline in ``create_table`` rather than added afterwards, which is what
lets them exist on SQLite too — 006 had to make its FK Postgres-only only because it was altering
an existing table, and SQLite has no ``ALTER`` for constraints.

``clinic_branches.aliases`` is step 6's, added here rather than in a 009 for the same reason
``held_until`` is here: 008 is not applied anywhere yet (007 is the deployed head), and a second
migration adding one column to a table no deployment has is a deploy for nothing. It is what lets
a patient say "المعادي" and reach the branch the workbook calls "Maadi" — the catalogue and the
branch list are written in English and the demo's patients are not, so the Arabic each one answers
to is data the clinic writes down rather than a dictionary in this repository.

The three uniqueness constraints on ``clinic_bookings`` are load-bearing and are described where
they are declared in ``apps/api/db/models.py``. The short version: one appointment per slot, one
reference per tenant, and one row per (tenant, conversation, slot) idempotency key so a retried
confirmation does not double-book.

Revision ID: 008_clinic
Revises: 007_unclaimed_deliveries
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008_clinic"
down_revision: str | None = "007_unclaimed_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Matches migration 004's ``APP_ROLE``. Not imported from there — Alembic migrations stand alone.
APP_ROLE = "watcher_app"

#: Created here, in dependency order; RLS is applied to each in the same order.
CLINIC_TABLES = (
    "clinic_branches",
    "clinic_services",
    "clinic_availability_slots",
    "clinic_bookings",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "clinic_branches",
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("area", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("placeholder", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("import_version", sa.String(length=64), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_clinic_branches_tenant_extid"),
    )
    op.create_index(
        op.f("ix_clinic_branches_tenant_id"), "clinic_branches", ["tenant_id"], unique=False
    )

    op.create_table(
        "clinic_services",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        # Minor units (piastres for EGP): a quoted price never goes through a float.
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EGP"),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("import_version", sa.String(length=64), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_clinic_services_tenant_code"),
    )
    op.create_index(
        op.f("ix_clinic_services_tenant_id"), "clinic_services", ["tenant_id"], unique=False
    )

    op.create_table(
        "clinic_availability_slots",
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        # Written by step 6's hold_slot; never by an import. Here rather than in a later migration
        # because the booking journey is what is built next.
        sa.Column("held_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("held_by_conversation_id", sa.Uuid(), nullable=True),
        sa.Column("import_version", sa.String(length=64), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["branch_id"], ["clinic_branches.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["clinic_services.id"]),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_clinic_slots_tenant_extid"),
    )
    op.create_index(
        op.f("ix_clinic_availability_slots_tenant_id"),
        "clinic_availability_slots",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_clinic_availability_slots_branch_id"),
        "clinic_availability_slots",
        ["branch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_clinic_availability_slots_service_id"),
        "clinic_availability_slots",
        ["service_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_clinic_availability_slots_starts_at"),
        "clinic_availability_slots",
        ["starts_at"],
        unique=False,
    )

    op.create_table(
        "clinic_bookings",
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("patient_name", sa.String(length=255), nullable=True),
        sa.Column("patient_phone", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="workbook"),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("import_version", sa.String(length=64), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["slot_id"], ["clinic_availability_slots.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.UniqueConstraint("tenant_id", "reference", name="uq_clinic_bookings_tenant_reference"),
        sa.UniqueConstraint("tenant_id", "slot_id", name="uq_clinic_bookings_tenant_slot"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_clinic_bookings_tenant_idempotency"
        ),
    )
    op.create_index(
        op.f("ix_clinic_bookings_tenant_id"), "clinic_bookings", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_clinic_bookings_slot_id"), "clinic_bookings", ["slot_id"], unique=False
    )
    op.create_index(
        op.f("ix_clinic_bookings_conversation_id"),
        "clinic_bookings",
        ["conversation_id"],
        unique=False,
    )

    if not _is_postgres():
        # SQLite has no RLS, and the test schema is built from ORM metadata anyway. The tenant
        # filter in the queries is what isolates there — the same split 004/005/006 make.
        return

    for table in CLINIC_TABLES:
        # Static DDL: values are fixed module constants, never runtime or user input.
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        op.execute(f"REVOKE ALL ON {table} FROM anon, authenticated;")
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                FOR ALL TO {APP_ROLE}
                USING (tenant_id = app.current_tenant())
                WITH CHECK (tenant_id = app.current_tenant());
        """)


def downgrade() -> None:
    if _is_postgres():
        for table in CLINIC_TABLES:
            # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
            # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
            # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    op.drop_index(op.f("ix_clinic_bookings_conversation_id"), table_name="clinic_bookings")
    op.drop_index(op.f("ix_clinic_bookings_slot_id"), table_name="clinic_bookings")
    op.drop_index(op.f("ix_clinic_bookings_tenant_id"), table_name="clinic_bookings")
    op.drop_table("clinic_bookings")

    for index in (
        "ix_clinic_availability_slots_starts_at",
        "ix_clinic_availability_slots_service_id",
        "ix_clinic_availability_slots_branch_id",
        "ix_clinic_availability_slots_tenant_id",
    ):
        op.drop_index(op.f(index), table_name="clinic_availability_slots")
    op.drop_table("clinic_availability_slots")

    op.drop_index(op.f("ix_clinic_services_tenant_id"), table_name="clinic_services")
    op.drop_table("clinic_services")

    op.drop_index(op.f("ix_clinic_branches_tenant_id"), table_name="clinic_branches")
    op.drop_table("clinic_branches")
