"""Add the fail-closed restored-snapshot runtime gate.

Revision ID: platform_recovery_gate_0001
Revises: retention_deletion_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "platform_recovery_gate_0001"
down_revision: str | None = "retention_deletion_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "recovery_gate",
        sa.Column("singleton", sa.Boolean(), primary_key=True, nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=True),
        sa.Column("ledger_id", sa.String(length=32), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("delivery_evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("payment_evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("usage_evidence_sha256", sa.String(length=64), nullable=True),
        sa.CheckConstraint("singleton = true", name="ck_platform_recovery_gate_singleton"),
        sa.CheckConstraint("state IN ('ready', 'fenced')", name="ck_platform_recovery_gate_state"),
        sa.CheckConstraint(
            "(snapshot_id IS NULL AND ledger_id IS NULL AND snapshot_at IS NULL "
            "AND state = 'ready') OR "
            "(snapshot_id IS NOT NULL AND ledger_id IS NOT NULL AND snapshot_at IS NOT NULL "
            "AND ledger_id > snapshot_id)",
            name="ck_platform_recovery_gate_artifact_order",
        ),
        schema="platform",
    )
    op.execute("INSERT INTO platform.recovery_gate (singleton, state) VALUES (true, 'ready')")


def downgrade() -> None:
    op.drop_table("recovery_gate", schema="platform")
