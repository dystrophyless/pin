"""Fence reactivated Accountability initial sends by delivery generation.

Revision ID: accountability_resume_0001
Revises: billing_access_lifecycle_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "accountability_resume_0001"
down_revision: str | None = "billing_access_lifecycle_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "delivery_intents",
        sa.Column("dispatch_version", sa.Integer(), server_default="1", nullable=False),
        schema="accountability",
    )
    op.create_check_constraint(
        "ck_accountability_intent_dispatch_version",
        "delivery_intents",
        "dispatch_version >= 1",
        schema="accountability",
    )
    op.alter_column(
        "delivery_intents",
        "dispatch_version",
        server_default=None,
        schema="accountability",
    )
    op.add_column(
        "delivery_intents",
        sa.Column(
            "retryable_after_access", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        schema="accountability",
    )
    op.alter_column(
        "delivery_intents",
        "retryable_after_access",
        server_default=None,
        schema="accountability",
    )


def downgrade() -> None:
    op.drop_column("delivery_intents", "retryable_after_access", schema="accountability")
    op.drop_constraint(
        "ck_accountability_intent_dispatch_version",
        "delivery_intents",
        schema="accountability",
        type_="check",
    )
    op.drop_column("delivery_intents", "dispatch_version", schema="accountability")
