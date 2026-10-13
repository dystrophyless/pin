"""Add bounded model call cost facts and normalized turn replay receipts.

Revision ID: conversation_model_0001
Revises: telegram_ingress_0001
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "conversation_model_0001"
down_revision: str | None = "telegram_ingress_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS conversation")
    op.create_table(
        "provider_calls",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=False),
        sa.Column("call_number", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("duplicate_of_call_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=18, scale=12), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("result_category", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("call_number BETWEEN 1 AND 4", name="ck_model_call_number"),
        sa.CheckConstraint(
            "state IN ('started', 'unknown', 'reported', 'deduplicated')",
            name="ck_model_call_state",
        ),
        sa.CheckConstraint(
            "(state = 'reported' AND prompt_tokens IS NOT NULL "
            "AND completion_tokens IS NOT NULL AND total_tokens IS NOT NULL) OR "
            "(state <> 'reported' AND prompt_tokens IS NULL "
            "AND completion_tokens IS NULL AND total_tokens IS NULL AND cost_usd IS NULL)",
            name="ck_model_usage_presence",
        ),
        sa.CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0",
                           name="ck_model_prompt_tokens"),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_model_completion_tokens",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens = prompt_tokens + completion_tokens",
            name="ck_model_total_tokens",
        ),
        sa.CheckConstraint("cost_usd IS NULL OR cost_usd >= 0",
                           name="ck_model_cost_nonnegative"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0",
                           name="ck_model_latency"),
        sa.CheckConstraint(
            "(state = 'deduplicated' AND duplicate_of_call_id IS NOT NULL) OR "
            "(state <> 'deduplicated' AND duplicate_of_call_id IS NULL)",
            name="ck_model_duplicate_link",
        ),
        sa.PrimaryKeyConstraint("call_id"),
        sa.UniqueConstraint("owner_id", "turn_id", "call_id", name="uq_model_call_identity"),
        sa.UniqueConstraint("provider_request_id", name="uq_model_provider_request"),
        schema="conversation",
    )
    op.create_index(
        "ix_model_calls_owner_turn",
        "provider_calls",
        ["owner_id", "turn_id", "call_number"],
        schema="conversation",
    )
    op.create_index(
        "ix_model_calls_state_created",
        "provider_calls",
        ["state", "created_at"],
        schema="conversation",
    )
    op.create_table(
        "turn_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("provider_request_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "turn_id", name="uq_conversation_turn_result"),
        schema="conversation",
    )


def downgrade() -> None:
    op.drop_table("turn_results", schema="conversation")
    op.drop_index(
        "ix_model_calls_state_created", table_name="provider_calls", schema="conversation"
    )
    op.drop_index(
        "ix_model_calls_owner_turn", table_name="provider_calls", schema="conversation"
    )
    op.drop_table("provider_calls", schema="conversation")
    op.execute("DROP SCHEMA IF EXISTS conversation")
