"""Add gateway-owned Telegram ingestion mode, polling offset, and owner mapping.

Revision ID: telegram_ingress_0001
Revises: billing_access_0001
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "telegram_ingress_0001"
down_revision: str | None = "billing_access_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingress_bots",
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("next_update_offset", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("bot_id > 0", name="ck_telegram_ingress_bot_id"),
        sa.CheckConstraint("mode IN ('webhook', 'polling')", name="ck_telegram_ingress_mode"),
        sa.CheckConstraint(
            "(mode = 'webhook' AND next_update_offset IS NULL) OR "
            "(mode = 'polling' AND next_update_offset IS NOT NULL AND next_update_offset >= 0)",
            name="ck_telegram_ingress_offset_mode",
        ),
        sa.PrimaryKeyConstraint("bot_id"),
        schema="pin_telegram",
    )
    op.create_table(
        "owners",
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.CheckConstraint("bot_id > 0", name="ck_telegram_owner_bot_id"),
        sa.CheckConstraint("telegram_user_id > 0", name="ck_telegram_owner_user_id"),
        sa.PrimaryKeyConstraint("bot_id", "telegram_user_id"),
        sa.UniqueConstraint("owner_id", name="uq_telegram_owner_id"),
        schema="pin_telegram",
    )


def downgrade() -> None:
    op.drop_table("owners", schema="pin_telegram")
    op.drop_table("ingress_bots", schema="pin_telegram")
