"""Persist billing notices and minimal return identity links.

Revision ID: billing_access_lifecycle_0001
Revises: accountability_cycles_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "billing_access_lifecycle_0001"
down_revision: str | None = "accountability_cycles_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retained_return_owners",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("retained_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("owner_id", name="pk_billing_retained_return_owners"),
        sa.UniqueConstraint(
            "telegram_user_id", name="uq_billing_retained_return_owners_telegram_user_id"
        ),
        schema="billing",
    )
    op.create_table(
        "access_expiry_notices",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("period_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('expiry_24h', 'expiry_1h')", name="ck_billing_expiry_notice_kind"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "period_id"],
            ["billing.access_periods.owner_id", "billing.access_periods.period_id"],
            name="fk_billing_expiry_notice_period_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_access_expiry_notices"),
        sa.UniqueConstraint(
            "owner_id", "period_id", "kind", name="uq_billing_expiry_notice_period_kind"
        ),
        schema="billing",
    )
    op.create_index(
        "ix_billing_expiry_notice_due",
        "access_expiry_notices",
        ["emitted_at", "due_at"],
        schema="billing",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_billing_expiry_notice_due",
        table_name="access_expiry_notices",
        schema="billing",
    )
    op.drop_table("access_expiry_notices", schema="billing")
    op.drop_table("retained_return_owners", schema="billing")
