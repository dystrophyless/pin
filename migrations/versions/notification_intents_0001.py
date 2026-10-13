"""Add versioned durable reminder intentions owned by Notifications.

Revision ID: notification_intents_0001
Revises: planning_reminder_policy_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "notification_intents_0001"
down_revision: str | None = "planning_reminder_policy_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS notifications")
    op.create_table(
        "reminder_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("plan_item_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("series_id", sa.Uuid(), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("occurrence_version", sa.Integer(), nullable=False),
        sa.Column("preferences_version", sa.Integer(), nullable=False),
        sa.Column("series_version", sa.Integer(), nullable=True),
        sa.Column("offset_kind", sa.String(length=24), nullable=False),
        sa.Column("offset_amount", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("dispatch_version", sa.Integer(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("plan_version >= 1", name="ck_notifications_intent_plan_version"),
        sa.CheckConstraint(
            "occurrence_version >= 1", name="ck_notifications_intent_occurrence_version"
        ),
        sa.CheckConstraint(
            "preferences_version >= 1", name="ck_notifications_intent_preferences_version"
        ),
        sa.CheckConstraint(
            "series_version IS NULL OR series_version >= 1",
            name="ck_notifications_intent_series_version",
        ),
        sa.CheckConstraint(
            "dispatch_version >= 1", name="ck_notifications_intent_dispatch_version"
        ),
        sa.CheckConstraint(
            "offset_kind IN ('calendar_days', 'elapsed_seconds')",
            name="ck_notifications_intent_offset_kind",
        ),
        sa.CheckConstraint("offset_amount > 0", name="ck_notifications_intent_offset_amount"),
        sa.CheckConstraint(
            "source IN ('default', 'explicit')", name="ck_notifications_intent_source"
        ),
        sa.CheckConstraint(
            "state IN ('scheduled', 'invalidated')", name="ck_notifications_intent_state"
        ),
        sa.CheckConstraint(
            "(series_id IS NULL AND series_version IS NULL) OR "
            "(series_id IS NOT NULL AND series_version IS NOT NULL)",
            name="ck_notifications_intent_series_pair",
        ),
        sa.ForeignKeyConstraint(
            ["plan_item_id", "owner_id"],
            ["planning.plan_items.id", "planning.plan_items.owner_id"],
            ondelete="CASCADE",
            name="fk_notifications_intent_plan_owner",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_notifications_intent_occurrence_owner",
        ),
        sa.ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["planning.series.id", "planning.series.owner_id"],
            ondelete="CASCADE",
            name="fk_notifications_intent_series_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications_reminder_intents"),
        sa.UniqueConstraint(
            "owner_id",
            "occurrence_id",
            "offset_kind",
            "offset_amount",
            name="uq_notifications_intent_semantic_key",
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_notifications_intent_owner"),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_intents_due",
        "reminder_intents",
        ["state", "due_at"],
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_intents_owner_plan",
        "reminder_intents",
        ["owner_id", "plan_item_id", "state"],
        schema="notifications",
    )
    op.create_table(
        "reconciliation_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("invalidated_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'stale')", name="ck_notifications_receipt_status"
        ),
        sa.CheckConstraint("created_count >= 0", name="ck_notifications_receipt_created"),
        sa.CheckConstraint("updated_count >= 0", name="ck_notifications_receipt_updated"),
        sa.CheckConstraint("invalidated_count >= 0", name="ck_notifications_receipt_invalidated"),
        sa.PrimaryKeyConstraint("id", name="pk_notifications_reconciliation_receipts"),
        sa.UniqueConstraint(
            "owner_id", "event_id", name="uq_notifications_receipt_owner_event"
        ),
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_table("reconciliation_receipts", schema="notifications")
    op.drop_index(
        "ix_notifications_intents_owner_plan",
        table_name="reminder_intents",
        schema="notifications",
    )
    op.drop_index(
        "ix_notifications_intents_due",
        table_name="reminder_intents",
        schema="notifications",
    )
    op.drop_table("reminder_intents", schema="notifications")
