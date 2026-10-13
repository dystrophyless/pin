"""Add durable platform inbox, jobs, outbox, receipts, and worker fencing.

Revision ID: platform_work_0001
Revises: telegram_callback_tokens_0001
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "platform_work_0001"
down_revision: str | None = "telegram_callback_tokens_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("platform"))
    op.create_table(
        "inbox",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("update_type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("bot_id", "update_id", name="uq_platform_inbox_bot_update"),
        schema="platform",
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("lane", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deduplication_key", sa.String(length=180), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("conversation_sequence", sa.BigInteger(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("lease_token", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "lane IN ('application', 'speech', 'delivery')", name="ck_platform_job_lane"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', 'dead')", name="ck_platform_job_status"
        ),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="ck_platform_job_attempts"),
        sa.CheckConstraint(
            "conversation_sequence IS NULL OR (owner_id IS NOT NULL AND conversation_sequence > 0)",
            name="ck_platform_job_conversation_sequence",
        ),
        sa.UniqueConstraint("lane", "deduplication_key", name="uq_platform_jobs_lane_dedupe"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_jobs_claim",
        "jobs",
        ["lane", "status", "available_at", "priority"],
        schema="platform",
    )
    op.create_index(
        "ix_platform_jobs_owner_order",
        "jobs",
        ["owner_id", "conversation_sequence", "status"],
        schema="platform",
    )
    op.create_table(
        "conversation_cursors",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False),
        schema="platform",
    )
    op.create_table(
        "conversation_leases",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("lease_token", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("fencing_generation", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fencing_generation >= 1", name="ck_platform_fencing_generation"),
        schema="platform",
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("causation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(event_type) BETWEEN 1 AND 120", name="ck_platform_event_type"),
        sa.CheckConstraint(
            "schema_version >= 1 AND aggregate_version >= 1", name="ck_platform_event_version"
        ),
        schema="platform",
    )
    op.create_index(
        "ix_platform_outbox_pending", "outbox", ["published_at", "created_at"], schema="platform"
    )
    op.create_index(
        "ix_platform_outbox_aggregate",
        "outbox",
        ["aggregate_id", "aggregate_version"],
        schema="platform",
    )
    op.create_table(
        "outbox_consumptions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("consumer_name", sa.String(length=120), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'stale')", name="ck_platform_consumption_status"
        ),
        sa.UniqueConstraint("consumer_name", "event_id", name="uq_platform_consumer_event"),
        schema="platform",
    )
    op.create_index(
        "uq_platform_consumer_version",
        "outbox_consumptions",
        ["consumer_name", "aggregate_id", "aggregate_version"],
        unique=True,
        schema="platform",
        postgresql_where=sa.text("status <> 'stale'"),
    )
    op.create_index(
        "ix_platform_consumption_reconcile",
        "outbox_consumptions",
        ["consumer_name", "aggregate_id", "status", "aggregate_version"],
        schema="platform",
    )
    op.create_table(
        "consumer_cursors",
        sa.Column("consumer_name", sa.String(length=120), primary_key=True, nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("last_version", sa.BigInteger(), nullable=False),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_table("consumer_cursors", schema="platform")
    op.drop_index(
        "ix_platform_consumption_reconcile", table_name="outbox_consumptions", schema="platform"
    )
    op.drop_table("outbox_consumptions", schema="platform")
    op.drop_index("ix_platform_outbox_aggregate", table_name="outbox", schema="platform")
    op.drop_index("ix_platform_outbox_pending", table_name="outbox", schema="platform")
    op.drop_table("outbox", schema="platform")
    op.drop_table("conversation_leases", schema="platform")
    op.drop_table("conversation_cursors", schema="platform")
    op.drop_index("ix_platform_jobs_owner_order", table_name="jobs", schema="platform")
    op.drop_index("ix_platform_jobs_claim", table_name="jobs", schema="platform")
    op.drop_table("jobs", schema="platform")
    op.drop_table("inbox", schema="platform")
    op.execute(sa.schema.DropSchema("platform"))
