"""Persist trial activation history across account deletion and return."""

import sqlalchemy as sa
from alembic import op

revision: str = "billing_trial_activation_0001"
down_revision: str | None = "identity_profile_0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "trial_activations",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("period_id", sa.String(length=128), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("owner_id", name="pk_billing_trial_activations"),
        sa.UniqueConstraint("telegram_user_id", name="uq_billing_trial_telegram_owner"),
        schema="billing",
    )


def downgrade() -> None:
    op.drop_table("trial_activations", schema="billing")
