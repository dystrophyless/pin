"""Record reminder send attempts and consumer-visible outcomes.

Revision ID: notification_delivery_0001
Revises: notification_intents_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "notification_delivery_0001"
down_revision: str | None = "notification_intents_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reminder_intents",
        sa.Column("not_before_at", sa.DateTime(timezone=True), nullable=True),
        schema="notifications",
    )
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=True),
        sa.Column("reply_event_id", sa.Uuid(), nullable=True),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("dispatch_version", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("check_in_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("access_period_id", sa.String(length=120), nullable=True),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("provider_message_id", sa.String(length=100), nullable=True),
        sa.Column("quiet_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("dispatch_version >= 1", name="ck_notifications_delivery_version"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_notifications_delivery_attempt"),
        sa.CheckConstraint(
            "(intent_id IS NOT NULL AND reply_event_id IS NULL) OR "
            "(intent_id IS NULL AND reply_event_id IS NOT NULL)",
            name="ck_notifications_delivery_subject",
        ),
        sa.CheckConstraint(
            "target_id IS NULL OR target_id > 0", name="ck_notifications_delivery_target"
        ),
        sa.CheckConstraint(
            "outcome IN ('started', 'confirmed', 'failed', 'unknown', 'blocked', "
            "'skipped', 'deferred', 'coalesced')",
            name="ck_notifications_delivery_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'started' AND finished_at IS NULL) OR "
            "(outcome <> 'started' AND finished_at IS NOT NULL)",
            name="ck_notifications_delivery_finished",
        ),
        sa.ForeignKeyConstraint(
            ["intent_id", "owner_id"],
            ["notifications.reminder_intents.id", "notifications.reminder_intents.owner_id"],
            ondelete="CASCADE",
            name="fk_notifications_delivery_intent_owner",
        ),
        sa.ForeignKeyConstraint(
            ["reply_event_id"],
            ["platform.outbox.id"],
            ondelete="CASCADE",
            name="fk_notifications_delivery_reply_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications_delivery_attempts"),
        sa.UniqueConstraint(
            "intent_id",
            "dispatch_version",
            "target_id",
            "attempt_number",
            name="uq_notifications_delivery_attempt",
        ),
        sa.UniqueConstraint(
            "reply_event_id",
            "dispatch_version",
            "target_id",
            "attempt_number",
            name="uq_notifications_delivery_reply_attempt",
        ),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_delivery_owner_outcome",
        "delivery_attempts",
        ["owner_id", "outcome", "started_at"],
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_delivery_cycle",
        "delivery_attempts",
        ["check_in_cycle_id", "outcome"],
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_delivery_cycle", table_name="delivery_attempts", schema="notifications"
    )
    op.drop_index(
        "ix_notifications_delivery_owner_outcome",
        table_name="delivery_attempts",
        schema="notifications",
    )
    op.drop_table("delivery_attempts", schema="notifications")
    op.drop_column("reminder_intents", "not_before_at", schema="notifications")
