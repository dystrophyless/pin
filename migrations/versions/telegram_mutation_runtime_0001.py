"""Persist owner-bound Telegram mutation clarification state.

Revision ID: telegram_mutation_runtime_0001
Revises: planning_home_zone_changes_0001
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "telegram_mutation_runtime_0001"
down_revision: str | None = "planning_home_zone_changes_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mutation_runtime_pending",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("expected_occurrence_version", sa.Integer(), nullable=False),
        sa.Column("expected_series_version", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column(
            "pending_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_mutation_runtime_pending_version"),
        sa.CheckConstraint(
            "expected_occurrence_version >= 1",
            name="ck_mutation_runtime_pending_occurrence_version",
        ),
        sa.CheckConstraint(
            "expected_series_version IS NULL OR expected_series_version >= 1",
            name="ck_mutation_runtime_pending_series_version",
        ),
        sa.CheckConstraint(
            "action IN ('complete', 'cancel', 'reschedule')",
            name="ck_mutation_runtime_pending_action",
        ),
        sa.CheckConstraint(
            "scope IS NULL OR scope IN ('this_occurrence', 'this_and_future')",
            name="ck_mutation_runtime_pending_scope",
        ),
        sa.CheckConstraint(
            "state IN ('awaiting_anchor', 'awaiting_scope', 'awaiting_schedule', "
            "'previewed', 'confirmed', 'dismissed', 'stale')",
            name="ck_mutation_runtime_pending_state",
        ),
        sa.CheckConstraint("language IN ('en', 'ru')", name="ck_mutation_runtime_pending_language"),
        sa.PrimaryKeyConstraint("id", name="pk_mutation_runtime_pending"),
        schema="pin_telegram",
    )
    op.create_index(
        "ix_mutation_runtime_pending_owner_state_expiry",
        "mutation_runtime_pending",
        ["owner_id", "state", "expires_at"],
        schema="pin_telegram",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mutation_runtime_pending_owner_state_expiry",
        table_name="mutation_runtime_pending",
        schema="pin_telegram",
    )
    op.drop_table("mutation_runtime_pending", schema="pin_telegram")
