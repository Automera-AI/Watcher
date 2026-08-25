"""Durable quarantine for webhook changes received before endpoint configuration.

Revision ID: 007_unclaimed_deliveries
Revises: 006_properties
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007_unclaimed_deliveries"
down_revision: str | None = "006_properties"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unclaimed_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_unclaimed_deliveries_endpoint_id"),
        "unclaimed_deliveries",
        ["endpoint_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_unclaimed_deliveries_endpoint_id"), table_name="unclaimed_deliveries")
    op.drop_table("unclaimed_deliveries")
