"""channel-neutral renames

Revision ID: 002_channel_neutral
Revises: f08a4a90af44
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "002_channel_neutral"
down_revision: str | None = "f08a4a90af44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- messages table ---
    op.alter_column("messages", "wa_message_id", new_column_name="external_id")
    op.alter_column("messages", "wa_chat_id", new_column_name="thread_id")
    op.alter_column("messages", "sender_wa_name", new_column_name="sender_display_name")
    op.add_column(
        "messages",
        sa.Column("channel", sa.String(length=32), server_default="whatsapp", nullable=False),
    )
    op.drop_constraint("uq_messages_tenant_wamid", "messages", type_="unique")
    op.create_unique_constraint("uq_messages_tenant_extid", "messages", ["tenant_id", "external_id"])
    op.drop_index("ix_messages_wa_chat_id", table_name="messages")
    op.create_index(op.f("ix_messages_thread_id"), "messages", ["thread_id"], unique=False)

    # --- sources table ---
    op.alter_column("sources", "wa_chat_id", new_column_name="thread_id")
    op.drop_constraint("uq_sources_tenant_chat", "sources", type_="unique")
    op.create_unique_constraint("uq_sources_tenant_thread", "sources", ["tenant_id", "thread_id"])

    # --- tenants table: move waba_id / phone_number_id into channel_configs ---
    op.create_table(
        "channel_configs",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "external_id", name="uq_channel_kind_extid"),
    )
    op.create_index(
        op.f("ix_channel_configs_tenant_id"), "channel_configs", ["tenant_id"], unique=False
    )

    op.execute(
        """
        INSERT INTO channel_configs (id, tenant_id, kind, external_id, config, enabled, created_at)
        SELECT
            gen_random_uuid(),
            id,
            'whatsapp',
            COALESCE(phone_number_id, ''),
            json_build_object('waba_id', waba_id, 'phone_number_id', phone_number_id),
            true,
            now()
        FROM tenants
        WHERE waba_id IS NOT NULL OR phone_number_id IS NOT NULL
        """
    )

    op.drop_column("tenants", "waba_id")
    op.drop_column("tenants", "phone_number_id")


def downgrade() -> None:
    # --- tenants: restore waba_id / phone_number_id ---
    op.add_column("tenants", sa.Column("waba_id", sa.String(length=64), nullable=True))
    op.add_column("tenants", sa.Column("phone_number_id", sa.String(length=64), nullable=True))

    op.execute(
        """
        UPDATE tenants SET
            waba_id = cc.config->>'waba_id',
            phone_number_id = cc.config->>'phone_number_id'
        FROM channel_configs cc
        WHERE cc.tenant_id = tenants.id AND cc.kind = 'whatsapp'
        """
    )

    op.drop_index(op.f("ix_channel_configs_tenant_id"), table_name="channel_configs")
    op.drop_table("channel_configs")

    # --- sources ---
    op.drop_constraint("uq_sources_tenant_thread", "sources", type_="unique")
    op.alter_column("sources", "thread_id", new_column_name="wa_chat_id")
    op.create_unique_constraint("uq_sources_tenant_chat", "sources", ["tenant_id", "wa_chat_id"])

    # --- messages ---
    op.drop_index(op.f("ix_messages_thread_id"), table_name="messages")
    op.drop_constraint("uq_messages_tenant_extid", "messages", type_="unique")
    op.drop_column("messages", "channel")
    op.alter_column("messages", "sender_display_name", new_column_name="sender_wa_name")
    op.alter_column("messages", "thread_id", new_column_name="wa_chat_id")
    op.alter_column("messages", "external_id", new_column_name="wa_message_id")
    op.create_unique_constraint(
        "uq_messages_tenant_wamid", "messages", ["tenant_id", "wa_message_id"]
    )
    op.create_index("ix_messages_wa_chat_id", "messages", ["wa_chat_id"], unique=False)
