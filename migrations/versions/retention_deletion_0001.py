"""Persist owner-confirmed deletion and post-snapshot replay facts.

Revision ID: retention_deletion_0001
Revises: platform_worker_heartbeats_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "retention_deletion_0001"
down_revision: str | None = "platform_worker_heartbeats_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS retention")
    op.add_column(
        "access_accounts",
        sa.Column("deletion_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="billing",
    )
    op.create_table(
        "deletion_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_retention_deletion_request_version"),
        sa.CheckConstraint(
            "state IN ('previewed', 'cancelled', 'pending_export', 'completed')",
            name="ck_retention_deletion_request_state",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_retention_deletion_requests"),
        schema="retention",
    )
    op.create_index(
        "ix_retention_deletion_owner_state",
        "deletion_requests",
        ["owner_id", "state"],
        schema="retention",
    )
    op.create_table(
        "deletion_tombstones",
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("artifact_version = 1", name="ck_retention_tombstone_artifact_version"),
        sa.PrimaryKeyConstraint("event_id", name="pk_retention_deletion_tombstones"),
        schema="retention",
    )
    op.create_index(
        "ix_retention_tombstone_export",
        "deletion_tombstones",
        ["exported_at"],
        schema="retention",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retention_tombstone_export", table_name="deletion_tombstones", schema="retention"
    )
    op.drop_table("deletion_tombstones", schema="retention")
    op.drop_index(
        "ix_retention_deletion_owner_state", table_name="deletion_requests", schema="retention"
    )
    op.drop_table("deletion_requests", schema="retention")
    op.drop_column("access_accounts", "deletion_pending", schema="billing")
    op.execute("DROP SCHEMA IF EXISTS retention")
