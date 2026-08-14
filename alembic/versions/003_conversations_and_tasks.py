"""conversations and tasks

Revision ID: 003_conversations
Revises: 002_channel_neutral
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "003_conversations"
down_revision: str | None = "002_channel_neutral"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("alternate_phones", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("external_system", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "phone_e164", name="uq_contacts_tenant_phone"),
    )
    op.create_index(op.f("ix_contacts_tenant_id"), "contacts", ["tenant_id"], unique=False)

    op.create_table(
        "conversations",
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("channel_thread_id", sa.String(length=128), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("identity_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], name="fk_conversations_contact"),
    )
    op.create_index(
        op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"], unique=False,
    )
    op.create_index(
        "ix_conversations_active_thread",
        "conversations",
        ["tenant_id", "channel_thread_id"],
        unique=False,
    )

    op.create_table(
        "turns",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("modality", sa.String(length=16), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("speech_confidence", sa.Float(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name="fk_turns_conv"),
        sa.UniqueConstraint("idempotency_key", name="uq_turns_idempotency"),
    )
    op.create_index(op.f("ix_turns_tenant_id"), "turns", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_turns_conversation_id"), "turns", ["conversation_id"], unique=False)

    op.create_table(
        "task_rows",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("slots", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("slots_confirmed", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="collecting"),
        sa.Column("outcome_ref", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_task_rows_conv",
        ),
    )
    op.create_index(op.f("ix_task_rows_tenant_id"), "task_rows", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_task_rows_conversation_id"), "task_rows", ["conversation_id"], unique=False,
    )

    op.create_table(
        "understandings",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("slots", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("confidence_overall", sa.Float(), nullable=False),
        sa.Column("confidence_intent", sa.Float(), nullable=False),
        sa.Column("confidence_person", sa.Float(), nullable=False),
        sa.Column("confidence_company", sa.Float(), nullable=False),
        sa.Column("autonomy", sa.String(length=16), nullable=True),
        sa.Column("model_used", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], name="fk_understandings_turn"),
        sa.ForeignKeyConstraint(["task_id"], ["task_rows.id"], name="fk_understandings_task"),
    )
    op.create_index(
        op.f("ix_understandings_tenant_id"), "understandings", ["tenant_id"], unique=False,
    )
    op.create_index(
        op.f("ix_understandings_turn_id"), "understandings", ["turn_id"], unique=False,
    )

    op.create_table(
        "corrections",
        sa.Column("understanding_id", sa.Uuid(), nullable=False),
        sa.Column("original_json", sa.JSON(), nullable=False),
        sa.Column("corrected_json", sa.JSON(), nullable=False),
        sa.Column("corrected_via", sa.String(length=64), nullable=True),
        sa.Column("promoted_to_golden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["understanding_id"], ["understandings.id"], name="fk_corrections_understanding",
        ),
    )
    op.create_index(
        op.f("ix_corrections_tenant_id"), "corrections", ["tenant_id"], unique=False,
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Float(), nullable=True),
        sa.Column("ref_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_usage_events_tenant_id"), "usage_events", ["tenant_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_events_tenant_id"), table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_index(op.f("ix_corrections_tenant_id"), table_name="corrections")
    op.drop_table("corrections")

    op.drop_index(op.f("ix_understandings_turn_id"), table_name="understandings")
    op.drop_index(op.f("ix_understandings_tenant_id"), table_name="understandings")
    op.drop_table("understandings")

    op.drop_index(op.f("ix_task_rows_conversation_id"), table_name="task_rows")
    op.drop_index(op.f("ix_task_rows_tenant_id"), table_name="task_rows")
    op.drop_table("task_rows")

    op.drop_index(op.f("ix_turns_conversation_id"), table_name="turns")
    op.drop_index(op.f("ix_turns_tenant_id"), table_name="turns")
    op.drop_table("turns")

    op.drop_index("ix_conversations_active_thread", table_name="conversations")
    op.drop_index(op.f("ix_conversations_tenant_id"), table_name="conversations")
    op.drop_table("conversations")

    op.drop_index(op.f("ix_contacts_tenant_id"), table_name="contacts")
    op.drop_table("contacts")
