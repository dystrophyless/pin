"""Create Planning's one-off items, occurrences, and reminder rules.

Revision ID: planning_one_off_0001
Revises: billing_trial_activation_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "planning_one_off_0001"
down_revision: str | None = "billing_trial_activation_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA planning")
    op.create_table(
        "plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('appointment', 'task', 'deadline', 'untimed')",
            name="ck_planning_plan_kind",
        ),
        sa.CheckConstraint("version >= 1", name="ck_planning_plan_version"),
        sa.PrimaryKeyConstraint("id", name="pk_planning_plan_items"),
        sa.UniqueConstraint("id", "owner_id", name="uq_planning_plan_owner"),
        schema="planning",
    )
    op.create_table(
        "occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("plan_item_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("event_zone", sa.String(length=128), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("local_time", sa.Time(timezone=False), nullable=True),
        sa.Column("fold", sa.String(length=8), nullable=True),
        sa.Column("end_local_date", sa.Date(), nullable=True),
        sa.Column("end_local_time", sa.Time(timezone=False), nullable=True),
        sa.Column("end_fold", sa.String(length=8), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('open', 'completed', 'cancelled')", name="ck_planning_occurrence_state"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_planning_occurrence_version"),
        sa.CheckConstraint("fold IS NULL OR fold IN ('earlier', 'later')", name="ck_planning_fold"),
        sa.CheckConstraint(
            "end_fold IS NULL OR end_fold IN ('earlier', 'later')", name="ck_planning_end_fold"
        ),
        sa.CheckConstraint(
            "(kind = 'untimed' AND local_time IS NULL AND starts_at IS NULL "
            "AND end_local_time IS NULL AND ends_at IS NULL AND deadline_at IS NULL "
            "AND duration_seconds IS NULL) OR "
            "(kind = 'deadline' AND local_time IS NOT NULL AND starts_at IS NULL "
            "AND end_local_time IS NULL AND ends_at IS NULL AND deadline_at IS NOT NULL "
            "AND duration_seconds IS NULL) OR "
            "(kind = 'appointment' AND local_time IS NOT NULL AND starts_at IS NOT NULL "
            "AND ends_at IS NOT NULL AND deadline_at IS NULL AND duration_seconds > 0 "
            "AND ((end_local_date IS NULL AND end_local_time IS NULL) OR "
            "(end_local_date IS NOT NULL AND end_local_time IS NOT NULL))) OR "
            "(kind = 'task' AND local_time IS NOT NULL AND starts_at IS NOT NULL "
            "AND end_local_time IS NULL AND ends_at IS NULL AND deadline_at IS NULL "
            "AND (duration_seconds IS NULL OR duration_seconds > 0))",
            name="ck_planning_occurrence_schedule_shape",
        ),
        sa.ForeignKeyConstraint(
            ["plan_item_id", "owner_id"],
            ["planning.plan_items.id", "planning.plan_items.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_occurrence_plan_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planning_occurrences"),
        sa.UniqueConstraint("id", "owner_id", name="uq_planning_occurrence_owner"),
        schema="planning",
    )
    op.create_index(
        "ix_planning_occurrences_owner_local_date",
        "occurrences",
        ["owner_id", "local_date", "kind"],
        schema="planning",
    )
    op.create_table(
        "reminder_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("offset_kind", sa.String(length=24), nullable=False),
        sa.Column("offset_amount", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "offset_kind IN ('calendar_days', 'elapsed_seconds')",
            name="ck_planning_reminder_offset_kind",
        ),
        sa.CheckConstraint("offset_amount > 0", name="ck_planning_reminder_offset_amount"),
        sa.CheckConstraint("source IN ('explicit', 'default')", name="ck_planning_reminder_source"),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_reminder_occurrence_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planning_reminder_rules"),
        sa.UniqueConstraint(
            "occurrence_id", "offset_kind", "offset_amount", name="uq_planning_reminder_offset"
        ),
        schema="planning",
    )
    op.create_index("ix_planning_reminders_due_at", "reminder_rules", ["due_at"], schema="planning")


def downgrade() -> None:
    op.drop_index("ix_planning_reminders_due_at", table_name="reminder_rules", schema="planning")
    op.drop_table("reminder_rules", schema="planning")
    op.drop_index(
        "ix_planning_occurrences_owner_local_date", table_name="occurrences", schema="planning"
    )
    op.drop_table("occurrences", schema="planning")
    op.drop_table("plan_items", schema="planning")
    op.execute("DROP SCHEMA planning")
