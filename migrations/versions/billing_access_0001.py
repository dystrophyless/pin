"""Create billing-owned access periods and usage reservations.

Revision ID: billing_access_0001
Revises: platform_work_0001
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "billing_access_0001"
down_revision: str | None = "platform_work_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("billing"))
    op.create_table(
        "access_accounts",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema="billing",
    )
    op.create_table(
        "access_periods",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("period_id", sa.String(length=128), nullable=False),
        sa.Column("policy_kind", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_turn_limit", sa.Integer(), nullable=False),
        sa.Column("voice_second_limit", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "policy_kind IN ('trial', 'paid')", name="ck_billing_period_policy_kind"
        ),
        sa.CheckConstraint("starts_at < ends_at", name="ck_billing_period_window"),
        sa.CheckConstraint(
            "ai_turn_limit > 0 AND voice_second_limit > 0",
            name="ck_billing_period_allowance",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["billing.access_accounts.owner_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("owner_id", "period_id"),
        schema="billing",
    )
    op.create_index(
        "ix_billing_period_owner_window",
        "access_periods",
        ["owner_id", "starts_at", "ends_at"],
        schema="billing",
    )
    op.create_table(
        "usage_reservations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("period_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("reserved_ai_turns", sa.Integer(), nullable=False),
        sa.Column("reserved_voice_seconds", sa.Integer(), nullable=False),
        sa.Column("settled_ai_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("settled_voice_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="reserved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('reserved', 'settled', 'refunded', 'expired')",
            name="ck_billing_usage_state",
        ),
        sa.CheckConstraint(
            "reserved_ai_turns >= 0 AND reserved_voice_seconds >= 0 "
            "AND (reserved_ai_turns > 0 OR reserved_voice_seconds > 0)",
            name="ck_billing_usage_reserved_units",
        ),
        sa.CheckConstraint(
            "settled_ai_turns >= 0 AND settled_voice_seconds >= 0 "
            "AND settled_ai_turns <= reserved_ai_turns "
            "AND settled_voice_seconds <= reserved_voice_seconds",
            name="ck_billing_usage_settled_units",
        ),
        sa.CheckConstraint("created_at < expires_at", name="ck_billing_usage_expiry"),
        sa.ForeignKeyConstraint(
            ["owner_id", "period_id"],
            ["billing.access_periods.owner_id", "billing.access_periods.period_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_billing_usage_owner_request"),
        schema="billing",
    )
    op.create_index(
        "ix_billing_usage_expiry", "usage_reservations", ["state", "expires_at"], schema="billing"
    )
    op.create_index(
        "ix_billing_usage_period_state",
        "usage_reservations",
        ["owner_id", "period_id", "state"],
        schema="billing",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_billing_usage_period_state", table_name="usage_reservations", schema="billing"
    )
    op.drop_index("ix_billing_usage_expiry", table_name="usage_reservations", schema="billing")
    op.drop_table("usage_reservations", schema="billing")
    op.drop_index("ix_billing_period_owner_window", table_name="access_periods", schema="billing")
    op.drop_table("access_periods", schema="billing")
    op.drop_table("access_accounts", schema="billing")
    op.execute(sa.schema.DropSchema("billing"))
