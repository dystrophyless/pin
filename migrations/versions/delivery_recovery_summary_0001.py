"""Persist recovery-summary attempt identity without changing the first delivery revision.

Revision ID: delivery_recovery_summary_0001
Revises: notification_delivery_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "delivery_recovery_summary_0001"
down_revision: str | None = "notification_delivery_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "delivery_attempts",
        sa.Column("recovery_episode_key", sa.String(length=180), nullable=True),
        schema="notifications",
    )
    op.drop_constraint(
        "ck_notifications_delivery_subject",
        "delivery_attempts",
        schema="notifications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_notifications_delivery_subject",
        "delivery_attempts",
        "(intent_id IS NOT NULL AND reply_event_id IS NULL AND "
        "recovery_episode_key IS NULL) OR "
        "(intent_id IS NULL AND reply_event_id IS NOT NULL AND "
        "recovery_episode_key IS NULL) OR "
        "(intent_id IS NULL AND reply_event_id IS NULL AND "
        "recovery_episode_key IS NOT NULL)",
        schema="notifications",
    )
    op.create_check_constraint(
        "ck_notifications_delivery_recovery_key",
        "delivery_attempts",
        "recovery_episode_key IS NULL OR length(recovery_episode_key) BETWEEN 1 AND 180",
        schema="notifications",
    )
    op.create_unique_constraint(
        "uq_notifications_delivery_recovery_attempt",
        "delivery_attempts",
        ["owner_id", "recovery_episode_key", "dispatch_version", "attempt_number"],
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_notifications_delivery_recovery_attempt",
        "delivery_attempts",
        schema="notifications",
        type_="unique",
    )
    op.drop_constraint(
        "ck_notifications_delivery_recovery_key",
        "delivery_attempts",
        schema="notifications",
        type_="check",
    )
    op.drop_constraint(
        "ck_notifications_delivery_subject",
        "delivery_attempts",
        schema="notifications",
        type_="check",
    )
    op.create_check_constraint(
        "ck_notifications_delivery_subject",
        "delivery_attempts",
        "(intent_id IS NOT NULL AND reply_event_id IS NULL) OR "
        "(intent_id IS NULL AND reply_event_id IS NOT NULL)",
        schema="notifications",
    )
    op.drop_column("delivery_attempts", "recovery_episode_key", schema="notifications")
