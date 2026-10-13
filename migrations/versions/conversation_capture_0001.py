"""Add durable text-capture drafts, input receipts and confirmation proofs.

Revision ID: conversation_capture_0001
Revises: delivery_recovery_summary_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "conversation_capture_0001"
down_revision: str | None = "delivery_recovery_summary_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS conversation")
    op.create_table(
        "capture_sequences",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint("last_sequence >= 0", name="ck_conversation_capture_seq_value"),
        sa.PrimaryKeyConstraint("owner_id", name="pk_conversation_capture_sequences"),
        schema="conversation",
    )
    op.create_table(
        "capture_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized_actions", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("projection_sha256", sa.String(length=64)),
        sa.Column("processing_key", sa.String(length=128)),
        sa.Column("confirmed_result", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_conversation_capture_version"),
        sa.CheckConstraint("sequence >= 1", name="ck_conversation_capture_sequence"),
        sa.CheckConstraint(
            "state IN ('collecting', 'processing', 'ready', 'confirmed', 'cancelled', "
            "'expired', 'failed')",
            name="ck_conversation_capture_state",
        ),
        sa.CheckConstraint(
            "(state = 'processing' AND processing_key IS NOT NULL) OR "
            "(state <> 'processing' AND processing_key IS NULL)",
            name="ck_conversation_capture_processing_key",
        ),
        sa.CheckConstraint(
            "(state = 'ready' AND normalized_actions IS NOT NULL AND preview IS NOT NULL "
            "AND projection_sha256 IS NOT NULL) OR state <> 'ready'",
            name="ck_conversation_capture_ready_projection",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_capture_drafts"),
        sa.UniqueConstraint("id", "owner_id", name="uq_conversation_capture_owner"),
        schema="conversation",
    )
    op.create_index(
        "uq_conversation_capture_active_owner",
        "capture_drafts",
        ["owner_id"],
        unique=True,
        schema="conversation",
        postgresql_where=sa.text("state IN ('collecting', 'processing', 'ready')"),
    )
    op.create_index(
        "ix_conversation_capture_expiry",
        "capture_drafts",
        ["state", "expires_at"],
        schema="conversation",
    )
    op.create_table(
        "capture_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("input_key", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_conversation_capture_input_sequence"),
        sa.CheckConstraint(
            "state IN ('processing', 'complete', 'failed', 'unknown')",
            name="ck_conversation_capture_input_state",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id", "owner_id"],
            ["conversation.capture_drafts.id", "conversation.capture_drafts.owner_id"],
            ondelete="CASCADE",
            name="fk_conversation_capture_input_draft_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_capture_inputs"),
        sa.UniqueConstraint("owner_id", "input_key", name="uq_conversation_capture_input_key"),
        schema="conversation",
    )
    op.create_index(
        "ix_conversation_capture_input_draft",
        "capture_inputs",
        ["draft_id", "sequence"],
        schema="conversation",
    )
    op.create_table(
        "capture_confirmations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("preview_version", sa.Integer(), nullable=False),
        sa.Column("part_key", sa.String(length=32), nullable=False),
        sa.Column("credential_sha256", sa.String(length=64), nullable=False),
        sa.Column("projection_sha256", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "preview_version >= 1", name="ck_conversation_capture_proof_version"
        ),
        sa.CheckConstraint(
            "length(projection_sha256) = 64", name="ck_conversation_capture_proof_digest"
        ),
        sa.CheckConstraint(
            "length(credential_sha256) = 64", name="ck_conversation_capture_credential_digest"
        ),
        sa.ForeignKeyConstraint(
            ["draft_id", "owner_id"],
            ["conversation.capture_drafts.id", "conversation.capture_drafts.owner_id"],
            ondelete="CASCADE",
            name="fk_conversation_capture_proof_draft_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_capture_confirmations"),
        sa.UniqueConstraint(
            "owner_id",
            "draft_id",
            "preview_version",
            "part_key",
            name="uq_conversation_capture_proof",
        ),
        schema="conversation",
    )
    op.create_index(
        "ix_conversation_capture_proof_expiry",
        "capture_confirmations",
        ["expires_at"],
        schema="conversation",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_capture_proof_expiry",
        table_name="capture_confirmations",
        schema="conversation",
    )
    op.drop_table("capture_confirmations", schema="conversation")
    op.drop_index(
        "ix_conversation_capture_input_draft",
        table_name="capture_inputs",
        schema="conversation",
    )
    op.drop_table("capture_inputs", schema="conversation")
    op.drop_index(
        "ix_conversation_capture_expiry", table_name="capture_drafts", schema="conversation"
    )
    op.drop_index(
        "uq_conversation_capture_active_owner",
        table_name="capture_drafts",
        schema="conversation",
    )
    op.drop_table("capture_drafts", schema="conversation")
    op.drop_table("capture_sequences", schema="conversation")
