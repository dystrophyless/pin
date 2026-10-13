"""Add Identity-owned preferences and durable onboarding drafts.

Revision ID: identity_profile_0001
Revises: conversation_model_0002
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "identity_profile_0001"
down_revision: str | None = "conversation_model_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS identity"))
    op.create_table(
        "preferences",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("home_zone", sa.String(length=128), nullable=False),
        sa.Column("reminder_calendar_days", sa.Integer(), nullable=False),
        sa.Column("reminder_elapsed_hours", sa.Integer(), nullable=False),
        sa.Column("quiet_start", sa.Time(timezone=False), nullable=False),
        sa.Column("quiet_end", sa.Time(timezone=False), nullable=False),
        sa.Column("review_time", sa.Time(timezone=False), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("language IN ('ru', 'en')", name="ck_identity_language"),
        sa.CheckConstraint("version >= 1", name="ck_identity_preferences_version"),
        sa.CheckConstraint(
            "reminder_calendar_days >= 0 AND reminder_elapsed_hours >= 0",
            name="ck_identity_reminder_offsets",
        ),
        sa.CheckConstraint("quiet_start <> quiet_end", name="ck_identity_quiet_interval"),
        sa.PrimaryKeyConstraint("owner_id"),
        schema="identity",
    )
    op.create_table(
        "onboarding",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_profile_version", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('initial', 'preferences')", name="ck_identity_onboarding_mode"
        ),
        sa.CheckConstraint(
            "step IN ('language', 'home_zone', 'reminders', 'quiet_review', 'confirm', 'complete')",
            name="ck_identity_onboarding_step",
        ),
        sa.CheckConstraint("version >= 1", name="ck_identity_onboarding_version"),
        sa.PrimaryKeyConstraint("owner_id"),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_table("onboarding", schema="identity")
    op.drop_table("preferences", schema="identity")
    op.execute(sa.text("DROP SCHEMA IF EXISTS identity"))
