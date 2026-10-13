"""Add durable, lease-fenced speech transcript continuation state.

Revision ID: speech_voice_0001
Revises: planning_mutation_previews_0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "speech_voice_0001"
down_revision: str | None = "planning_mutation_previews_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS speech")
    op.create_table(
        "requests",
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("inbox_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("request_key", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger()),
        sa.Column("declared_duration_seconds", sa.Float(), nullable=False),
        sa.Column("actual_size_bytes", sa.BigInteger()),
        sa.Column("decoded_duration_seconds", sa.Float()),
        sa.Column("transcript", sa.Text()),
        sa.Column("language", sa.String(length=2)),
        sa.Column("failure_code", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('processing', 'transcribed', 'continued', 'failed', 'rejected')",
            name="ck_speech_request_state",
        ),
        sa.CheckConstraint(
            "(state = 'transcribed' AND transcript IS NOT NULL AND language IS NOT NULL) OR "
            "(state <> 'transcribed' AND transcript IS NULL)",
            name="ck_speech_request_transcript_state",
        ),
        sa.CheckConstraint(
            "declared_size_bytes IS NULL OR declared_size_bytes > 0",
            name="ck_speech_declared_size_positive",
        ),
        sa.CheckConstraint(
            "declared_duration_seconds > 0", name="ck_speech_declared_duration_positive"
        ),
        sa.CheckConstraint(
            "actual_size_bytes IS NULL OR actual_size_bytes BETWEEN 1 AND 10000000",
            name="ck_speech_actual_size_bounds",
        ),
        sa.CheckConstraint(
            "decoded_duration_seconds IS NULL OR "
            "decoded_duration_seconds BETWEEN 0.000001 AND 180",
            name="ck_speech_decoded_duration_bounds",
        ),
        sa.CheckConstraint(
            "language IS NULL OR language IN ('ru', 'en')", name="ck_speech_language"
        ),
        sa.PrimaryKeyConstraint("job_id", name="pk_speech_requests"),
        sa.UniqueConstraint("request_key", name="uq_speech_request_key"),
        schema="speech",
    )
    op.create_index(
        "ix_speech_requests_owner_created",
        "requests",
        ["owner_id", "created_at"],
        schema="speech",
    )


def downgrade() -> None:
    op.drop_index("ix_speech_requests_owner_created", table_name="requests", schema="speech")
    op.drop_table("requests", schema="speech")
    op.execute("DROP SCHEMA IF EXISTS speech")
