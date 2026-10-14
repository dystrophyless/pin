"""Persist check-in cycles, daily reviews and their delivery intents.

Revision ID: accountability_cycles_0001
Revises: telegram_mutation_runtime_0001
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "accountability_cycles_0001"
down_revision: str | None = "telegram_mutation_runtime_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("accountability", if_not_exists=True))
    op.create_table(
        "check_in_cycles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("expected_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("initial_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nudge_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nudge_slot_used", sa.Boolean(), nullable=False),
        sa.Column("nudge_intent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('appointment', 'deadline')", name="ck_accountability_cycle_kind"
        ),
        sa.CheckConstraint(
            "state IN ('open', 'completed', 'cancelled', 'superseded', 'exhausted')",
            name="ck_accountability_cycle_state",
        ),
        sa.CheckConstraint("occurrence_version >= 1", name="ck_accountability_cycle_version"),
        sa.PrimaryKeyConstraint("id", name="pk_accountability_check_in_cycles"),
        schema="accountability",
    )
    op.create_index(
        "ix_accountability_cycle_due",
        "check_in_cycles",
        ["state", "expected_end_at"],
        schema="accountability",
    )
    op.create_index(
        "ix_accountability_cycle_owner_occurrence",
        "check_in_cycles",
        ["owner_id", "occurrence_id", "state"],
        schema="accountability",
    )

    op.create_table(
        "daily_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("zone_name", sa.String(length=128), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("destination_id", sa.BigInteger(), nullable=False),
        sa.Column("occurrence_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("initial_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nudge_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nudge_slot_used", sa.Boolean(), nullable=False),
        sa.Column("nudge_intent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('open', 'completed', 'cancelled', 'superseded', 'exhausted')",
            name="ck_accountability_review_state",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accountability_daily_reviews"),
        sa.UniqueConstraint("owner_id", "review_date", name="uq_accountability_review_owner_date"),
        schema="accountability",
    )
    op.create_index(
        "ix_accountability_review_due",
        "daily_reviews",
        ["state", "due_at"],
        schema="accountability",
    )

    op.create_table(
        "delivery_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=True),
        sa.Column("review_id", sa.Uuid(), nullable=True),
        sa.Column("slot", sa.String(length=8), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_outcome_attempt", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("slot IN ('initial', 'nudge')", name="ck_accountability_intent_slot"),
        sa.CheckConstraint(
            "(cycle_id IS NOT NULL AND review_id IS NULL) OR "
            "(cycle_id IS NULL AND review_id IS NOT NULL)",
            name="ck_accountability_intent_subject",
        ),
        sa.CheckConstraint(
            "state IN ('scheduled', 'sending', 'confirmed', 'failed', 'unknown', "
            "'skipped', 'exhausted')",
            name="ck_accountability_intent_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_accountability_intent_attempts"),
        sa.CheckConstraint(
            "last_outcome_attempt >= 0", name="ck_accountability_intent_outcome_attempt"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accountability_delivery_intents"),
        schema="accountability",
    )
    op.create_index(
        "uq_accountability_intent_cycle_slot",
        "delivery_intents",
        ["cycle_id", "slot"],
        unique=True,
        schema="accountability",
    )
    op.create_index(
        "uq_accountability_intent_review_slot",
        "delivery_intents",
        ["review_id", "slot"],
        unique=True,
        schema="accountability",
    )
    op.create_index(
        "ix_accountability_intent_due",
        "delivery_intents",
        ["state", "due_at"],
        schema="accountability",
    )

    op.create_table(
        "outcome_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accountability_outcome_receipts"),
        sa.UniqueConstraint("owner_id", "event_id", name="uq_accountability_outcome_owner_event"),
        schema="accountability",
    )

    op.create_table(
        "accountability_delivery_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("provider_message_id", sa.String(length=100), nullable=True),
        sa.Column("access_period_id", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_number >= 1", name="ck_notifications_accountability_attempt"),
        sa.CheckConstraint("target_id > 0", name="ck_notifications_accountability_target"),
        sa.CheckConstraint(
            "outcome IN ('started', 'confirmed', 'failed', 'unknown', 'blocked')",
            name="ck_notifications_accountability_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'started' AND finished_at IS NULL) OR "
            "(outcome <> 'started' AND finished_at IS NOT NULL)",
            name="ck_notifications_accountability_finished",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications_accountability_delivery_attempts"),
        sa.UniqueConstraint(
            "intent_id", "attempt_number", name="uq_notifications_accountability_attempt"
        ),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_accountability_intent_outcome",
        "accountability_delivery_attempts",
        ["intent_id", "outcome"],
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_accountability_intent_outcome",
        table_name="accountability_delivery_attempts",
        schema="notifications",
    )
    op.drop_table("accountability_delivery_attempts", schema="notifications")
    op.drop_table("outcome_receipts", schema="accountability")
    op.drop_index(
        "ix_accountability_intent_due", table_name="delivery_intents", schema="accountability"
    )
    op.drop_index(
        "uq_accountability_intent_review_slot",
        table_name="delivery_intents",
        schema="accountability",
    )
    op.drop_index(
        "uq_accountability_intent_cycle_slot",
        table_name="delivery_intents",
        schema="accountability",
    )
    op.drop_table("delivery_intents", schema="accountability")
    op.drop_index(
        "ix_accountability_review_due", table_name="daily_reviews", schema="accountability"
    )
    op.drop_table("daily_reviews", schema="accountability")
    op.drop_index(
        "ix_accountability_cycle_owner_occurrence",
        table_name="check_in_cycles",
        schema="accountability",
    )
    op.drop_index(
        "ix_accountability_cycle_due", table_name="check_in_cycles", schema="accountability"
    )
    op.drop_table("check_in_cycles", schema="accountability")
