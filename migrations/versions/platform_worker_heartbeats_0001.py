"""Record redacted liveness for independently running workers.

Revision ID: platform_worker_heartbeats_0001
Revises: billing_payments_0001
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "platform_worker_heartbeats_0001"
down_revision: str | None = "billing_payments_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('application', 'delivery', 'speech', 'gateway')",
            name="ck_platform_worker_heartbeat_role",
        ),
        sa.PrimaryKeyConstraint("worker_id", name="pk_platform_worker_heartbeats"),
        schema="platform",
    )
    op.create_index(
        "ix_platform_worker_heartbeats_role_seen",
        "worker_heartbeats",
        ["role", "last_seen_at"],
        schema="platform",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_worker_heartbeats_role_seen",
        table_name="worker_heartbeats",
        schema="platform",
    )
    op.drop_table("worker_heartbeats", schema="platform")
