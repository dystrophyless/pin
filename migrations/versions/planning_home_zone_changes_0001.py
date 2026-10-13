"""Persist owner-bound, versioned home-zone change previews.

Revision ID: planning_home_zone_changes_0001
Revises: speech_voice_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "planning_home_zone_changes_0001"
down_revision: str | None = "speech_voice_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "home_zone_change_previews",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("preview_version", sa.Integer(), nullable=False),
        sa.Column("projection_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("profile_version >= 1", name="ck_planning_zone_preview_profile_version"),
        sa.CheckConstraint("preview_version >= 1", name="ck_planning_zone_preview_version"),
        sa.CheckConstraint(
            "length(projection_sha256) = 64", name="ck_planning_zone_preview_digest"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'confirmed', 'cancelled')",
            name="ck_planning_zone_preview_state",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planning_home_zone_change_previews"),
        schema="planning",
    )
    op.create_index(
        "ix_planning_zone_preview_owner",
        "home_zone_change_previews",
        ["owner_id"],
        schema="planning",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_planning_zone_preview_owner",
        table_name="home_zone_change_previews",
        schema="planning",
    )
    op.drop_table("home_zone_change_previews", schema="planning")
