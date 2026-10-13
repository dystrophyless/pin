"""Add durable Planning recurrence cursor, occurrence keys and exceptions.

Revision ID: planning_recurrence_0001
Revises: agenda_pages_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "planning_recurrence_0001"
down_revision: str | None = "agenda_pages_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("plan_item_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("anchor_sequence", sa.Integer(), nullable=False),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("rule", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("typed_draft", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint("version >= 1", name="ck_planning_series_version"),
        sa.CheckConstraint("anchor_sequence >= 0", name="ck_planning_series_anchor_sequence"),
        sa.CheckConstraint(
            "next_sequence >= anchor_sequence", name="ck_planning_series_next_sequence"
        ),
        sa.CheckConstraint("state IN ('active', 'stopped')", name="ck_planning_series_state"),
        sa.ForeignKeyConstraint(
            ["plan_item_id", "owner_id"],
            ["planning.plan_items.id", "planning.plan_items.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_item_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planning_series"),
        sa.UniqueConstraint("id", "owner_id", name="uq_planning_series_owner"),
        sa.UniqueConstraint("plan_item_id", name="uq_planning_series_plan_item_id"),
        schema="planning",
    )
    op.create_table(
        "series_occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("series_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("recurrence_key", sa.String(length=128), nullable=False),
        sa.Column("dst_gap_shift_seconds", sa.Integer(), nullable=False),
        sa.Column("repeated_time_choice", sa.String(length=8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_planning_series_occurrence_sequence"),
        sa.CheckConstraint(
            "dst_gap_shift_seconds >= 0", name="ck_planning_series_gap_shift"
        ),
        sa.CheckConstraint(
            "repeated_time_choice IS NULL OR repeated_time_choice = 'earlier'",
            name="ck_planning_series_repeated_choice",
        ),
        sa.ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["planning.series.id", "planning.series.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_occurrence_series_owner",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_occurrence_plan_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planning_series_occurrences"),
        sa.UniqueConstraint(
            "series_id", "recurrence_key", name="uq_planning_series_recurrence_key"
        ),
        sa.UniqueConstraint("series_id", "sequence", name="uq_planning_series_sequence"),
        sa.UniqueConstraint("occurrence_id", name="uq_planning_series_occurrence_id"),
        schema="planning",
    )
    op.create_table(
        "series_exceptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("series_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("approved_change", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_planning_series_exception_sequence"),
        sa.CheckConstraint("version >= 1", name="ck_planning_series_exception_version"),
        sa.CheckConstraint(
            "scope IN ('single', 'this_and_future')", name="ck_planning_series_exception_scope"
        ),
        sa.ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["planning.series.id", "planning.series.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_exception_series_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planning_series_exceptions"),
        sa.UniqueConstraint(
            "series_id", "sequence", "version", name="uq_planning_series_exception_version"
        ),
        schema="planning",
    )


def downgrade() -> None:
    op.drop_table("series_exceptions", schema="planning")
    op.drop_table("series_occurrences", schema="planning")
    op.drop_table("series", schema="planning")
