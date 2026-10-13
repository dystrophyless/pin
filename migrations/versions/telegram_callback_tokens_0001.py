"""Add owner-bound, expiring, one-time Telegram callback tokens.

Revision ID: telegram_callback_tokens_0001
Revises: persistence_0001
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "telegram_callback_tokens_0001"
down_revision: str | None = "persistence_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("pin_telegram"))
    op.create_table(
        "callback_tokens",
        sa.Column("token_hash", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_callback_tokens_hash_length"),
        sa.CheckConstraint("target_version >= 0", name="ck_callback_tokens_target_version"),
        sa.CheckConstraint("length(action) BETWEEN 1 AND 64", name="ck_callback_tokens_action"),
        schema="pin_telegram",
    )
    op.create_index(
        "ix_pin_telegram_callback_tokens_owner_id",
        "callback_tokens",
        ["owner_id"],
        schema="pin_telegram",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pin_telegram_callback_tokens_owner_id",
        table_name="callback_tokens",
        schema="pin_telegram",
    )
    op.drop_table("callback_tokens", schema="pin_telegram")
    op.execute(sa.schema.DropSchema("pin_telegram"))
