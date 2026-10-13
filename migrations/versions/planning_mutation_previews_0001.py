"""Persist owner-bound mutation previews for restart-safe confirmation.

Revision ID: planning_mutation_previews_0001
Revises: conversation_capture_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "planning_mutation_previews_0001"
down_revision: str | None = "conversation_capture_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mutation_previews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_version", sa.Integer(), nullable=False),
        sa.Column("preferences_version", sa.Integer(), nullable=False),
        sa.Column("preview_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("projection_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("preview_version >= 1", name="ck_planning_mutation_preview_version"),
        sa.CheckConstraint(
            "action IN ('complete', 'cancel', 'reschedule')",
            name="ck_planning_mutation_preview_action",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'confirmed', 'cancelled')",
            name="ck_planning_mutation_preview_state",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_mutation_preview_occurrence_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_planning_mutation_previews"),
        schema="planning",
    )
    op.create_index(
        "ix_planning_mutation_previews_owner_id",
        "mutation_previews",
        ["owner_id"],
        schema="planning",
    )
    op.create_index(
        "ix_planning_mutation_previews_occurrence_id",
        "mutation_previews",
        ["occurrence_id"],
        schema="planning",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_planning_mutation_previews_occurrence_id",
        table_name="mutation_previews",
        schema="planning",
    )
    op.drop_index(
        "ix_planning_mutation_previews_owner_id",
        table_name="mutation_previews",
        schema="planning",
    )
    op.drop_table("mutation_previews", schema="planning")
