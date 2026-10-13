"""Record the Identity preference version approved for each occurrence schedule.

Revision ID: planning_reminder_policy_0001
Revises: planning_recurrence_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "planning_reminder_policy_0001"
down_revision: str | None = "planning_recurrence_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "occurrences",
        sa.Column("reminder_preferences_version", sa.Integer(), nullable=True),
        schema="planning",
    )


def downgrade() -> None:
    op.drop_column("occurrences", "reminder_preferences_version", schema="planning")
