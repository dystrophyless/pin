"""Create the persistence-owned command receipt table.

Revision ID: persistence_0001
Revises:
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "persistence_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("pin_persistence"))
    op.create_table(
        "action_receipts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action_key", sa.String(length=128), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(action_key)) BETWEEN 1 AND 128",
            name="ck_action_receipts_action_key",
        ),
        sa.UniqueConstraint("owner_id", "action_key", name="uq_action_receipts_owner_action"),
        schema="pin_persistence",
    )


def downgrade() -> None:
    op.drop_table("action_receipts", schema="pin_persistence")
    op.execute(sa.schema.DropSchema("pin_persistence"))
