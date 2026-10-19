"""Add Identity-owned current processing consent state.

Revision ID: identity_processing_consent_0001
Revises: platform_recovery_gate_0001
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "identity_processing_consent_0001"
down_revision: str | None = "platform_recovery_gate_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processing_consent",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="required"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_version", sa.String(length=64), nullable=True),
        sa.Column("grant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_language", sa.String(length=2), nullable=True),
        sa.Column("accepted_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("accepted_update_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('required', 'granted', 'revoked')",
            name="ck_identity_consent_state",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_identity_consent_revision"),
        sa.CheckConstraint(
            "accepted_language IS NULL OR accepted_language IN ('ru', 'en')",
            name="ck_identity_consent_language",
        ),
        sa.CheckConstraint(
            "accepted_telegram_user_id IS NULL OR accepted_telegram_user_id > 0",
            name="ck_identity_consent_telegram_id",
        ),
        sa.CheckConstraint(
            "(state = 'granted' AND policy_version IS NOT NULL AND grant_id IS NOT NULL "
            "AND accepted_at IS NOT NULL AND accepted_language IS NOT NULL "
            "AND accepted_telegram_user_id IS NOT NULL AND accepted_update_id IS NOT NULL "
            "AND revoked_at IS NULL) OR "
            "(state = 'revoked' AND policy_version IS NOT NULL AND grant_id IS NOT NULL "
            "AND accepted_at IS NOT NULL AND accepted_language IS NOT NULL "
            "AND accepted_telegram_user_id IS NOT NULL AND accepted_update_id IS NOT NULL "
            "AND revoked_at IS NOT NULL) OR "
            "(state = 'required' AND policy_version IS NULL AND grant_id IS NULL "
            "AND accepted_at IS NULL AND accepted_language IS NULL "
            "AND accepted_telegram_user_id IS NULL AND accepted_update_id IS NULL "
            "AND revoked_at IS NULL)",
            name="ck_identity_consent_state_metadata",
        ),
        sa.PrimaryKeyConstraint("owner_id", name="pk_identity_processing_consent"),
        sa.UniqueConstraint("grant_id", name="uq_identity_processing_consent_grant_id"),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_table("processing_consent", schema="identity")
