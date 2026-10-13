"""Retain independently reported provider cost and token facts.

Revision ID: conversation_model_0002
Revises: conversation_model_0001
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "conversation_model_0002"
down_revision: str | None = "conversation_model_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_model_usage_presence", "provider_calls", schema="conversation", type_="check"
    )
    op.create_check_constraint(
        "ck_model_usage_presence",
        "provider_calls",
        "(state = 'reported' AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL "
        "AND total_tokens IS NOT NULL) OR state = 'unknown' OR "
        "(state IN ('started', 'deduplicated') AND prompt_tokens IS NULL "
        "AND completion_tokens IS NULL AND total_tokens IS NULL AND cost_usd IS NULL)",
        schema="conversation",
    )
    op.drop_constraint(
        "ck_model_total_tokens", "provider_calls", schema="conversation", type_="check"
    )
    op.create_check_constraint(
        "ck_model_total_tokens",
        "provider_calls",
        "total_tokens IS NULL OR prompt_tokens IS NULL OR completion_tokens IS NULL "
        "OR total_tokens = prompt_tokens + completion_tokens",
        schema="conversation",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_model_total_tokens", "provider_calls", schema="conversation", type_="check"
    )
    op.create_check_constraint(
        "ck_model_total_tokens",
        "provider_calls",
        "total_tokens IS NULL OR total_tokens = prompt_tokens + completion_tokens",
        schema="conversation",
    )
    op.drop_constraint(
        "ck_model_usage_presence", "provider_calls", schema="conversation", type_="check"
    )
    op.create_check_constraint(
        "ck_model_usage_presence",
        "provider_calls",
        "(state = 'reported' AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL "
        "AND total_tokens IS NOT NULL) OR (state <> 'reported' AND prompt_tokens IS NULL "
        "AND completion_tokens IS NULL AND total_tokens IS NULL AND cost_usd IS NULL)",
        schema="conversation",
    )
