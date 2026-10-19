"""Persist processing provenance on admitted work and AI artifacts.

Revision ID: processing_consent_0001
Revises: identity_processing_consent_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "processing_consent_0001"
down_revision: str | None = "identity_processing_consent_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for schema, table in (
        ("platform", "inbox"),
        ("platform", "jobs"),
        ("conversation", "provider_calls"),
        ("conversation", "turn_results"),
        ("conversation", "capture_drafts"),
        ("conversation", "capture_inputs"),
        ("speech", "requests"),
    ):
        op.add_column(table, sa.Column("processing_grant_id", sa.Uuid(as_uuid=True)), schema=schema)
        op.add_column(
            table,
            sa.Column("processing_policy_version", sa.String(length=64)),
            schema=schema,
        )
        op.create_check_constraint(
            f"ck_{schema}_{table}_processing_provenance",
            table,
            "(processing_grant_id IS NULL AND processing_policy_version IS NULL) OR "
            "(processing_grant_id IS NOT NULL AND processing_policy_version IS NOT NULL)",
            schema=schema,
        )

    op.add_column(
        "mutation_runtime_pending",
        sa.Column("processing_grant_id", sa.Uuid(as_uuid=True)),
        schema="pin_telegram",
    )
    op.add_column(
        "mutation_runtime_pending",
        sa.Column("processing_policy_version", sa.String(length=64)),
        schema="pin_telegram",
    )
    op.create_check_constraint(
        "ck_mutation_runtime_pending_processing_provenance",
        "mutation_runtime_pending",
        "(processing_grant_id IS NULL AND processing_policy_version IS NULL) OR "
        "(processing_grant_id IS NOT NULL AND processing_policy_version IS NOT NULL)",
        schema="pin_telegram",
    )

    op.add_column(
        "recovery_gate",
        sa.Column("consent_invalidated_snapshot_id", sa.String(length=32)),
        schema="platform",
    )
    op.add_column(
        "recovery_gate",
        sa.Column("consent_invalidated_ledger_id", sa.String(length=32)),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mutation_runtime_pending_processing_provenance",
        "mutation_runtime_pending",
        schema="pin_telegram",
        type_="check",
    )
    op.drop_column(
        "mutation_runtime_pending", "processing_policy_version", schema="pin_telegram"
    )
    op.drop_column("mutation_runtime_pending", "processing_grant_id", schema="pin_telegram")
    op.drop_column("recovery_gate", "consent_invalidated_ledger_id", schema="platform")
    op.drop_column("recovery_gate", "consent_invalidated_snapshot_id", schema="platform")
    for schema, table in reversed(
        (
            ("platform", "inbox"),
            ("platform", "jobs"),
            ("conversation", "provider_calls"),
            ("conversation", "turn_results"),
            ("conversation", "capture_drafts"),
            ("conversation", "capture_inputs"),
            ("speech", "requests"),
        )
    ):
        op.drop_constraint(
            f"ck_{schema}_{table}_processing_provenance", table, schema=schema, type_="check"
        )
        op.drop_column(table, "processing_policy_version", schema=schema)
        op.drop_column(table, "processing_grant_id", schema=schema)
