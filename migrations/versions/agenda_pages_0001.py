"""Persist owner-bound agenda snapshots and page navigation.

Revision ID: agenda_pages_0001
Revises: planning_one_off_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "agenda_pages_0001"
down_revision: str | None = "planning_one_off_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agenda_snapshots",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("view", sa.String(length=8), nullable=False),
        sa.Column("display_zone", sa.String(length=128), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("include_completed", sa.Boolean(), nullable=False),
        sa.Column("item_ids", sa.JSON(), nullable=False),
        sa.Column("page_starts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("view IN ('today', 'week')", name="ck_planning_agenda_view"),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_planning_agenda_snapshots"),
        schema="planning",
    )
    op.create_index(
        "ix_planning_agenda_owner_expiry",
        "agenda_snapshots",
        ["owner_id", "expires_at"],
        schema="planning",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_planning_agenda_owner_expiry", table_name="agenda_snapshots", schema="planning"
    )
    op.drop_table("agenda_snapshots", schema="planning")
