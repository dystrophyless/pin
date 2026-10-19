"""Conversation-owned durable drafts, per-user sequencing, and confirmation proofs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class CaptureSequenceRecord(Base):
    __tablename__ = "capture_sequences"
    __table_args__ = (
        CheckConstraint("last_sequence >= 0", name="ck_conversation_capture_seq_value"),
        {"schema": "conversation"},
    )

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CaptureDraftRecord(Base):
    __tablename__ = "capture_drafts"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_conversation_capture_version"),
        CheckConstraint(
            "(processing_grant_id IS NULL AND processing_policy_version IS NULL) OR "
            "(processing_grant_id IS NOT NULL AND processing_policy_version IS NOT NULL)",
            name="ck_conversation_capture_drafts_processing_provenance",
        ),
        CheckConstraint("sequence >= 1", name="ck_conversation_capture_sequence"),
        CheckConstraint(
            "state IN ('collecting', 'processing', 'ready', 'confirmed', 'cancelled', "
            "'expired', 'failed')",
            name="ck_conversation_capture_state",
        ),
        CheckConstraint(
            "(state = 'processing' AND processing_key IS NOT NULL) OR "
            "(state <> 'processing' AND processing_key IS NULL)",
            name="ck_conversation_capture_processing_key",
        ),
        CheckConstraint(
            "(state = 'ready' AND normalized_actions IS NOT NULL AND preview IS NOT NULL "
            "AND projection_sha256 IS NOT NULL) OR state <> 'ready'",
            name="ck_conversation_capture_ready_projection",
        ),
        UniqueConstraint("id", "owner_id", name="uq_conversation_capture_owner"),
        Index(
            "uq_conversation_capture_active_owner",
            "owner_id",
            unique=True,
            postgresql_where=text("state IN ('collecting', 'processing', 'ready')"),
        ),
        Index("ix_conversation_capture_expiry", "state", "expires_at"),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    processing_grant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    processing_policy_version: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    messages: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    normalized_actions: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    preview: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    projection_sha256: Mapped[str | None] = mapped_column(String(64))
    processing_key: Mapped[str | None] = mapped_column(String(128))
    confirmed_result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaptureInputRecord(Base):
    __tablename__ = "capture_inputs"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_conversation_capture_input_sequence"),
        CheckConstraint(
            "state IN ('processing', 'complete', 'failed', 'unknown')",
            name="ck_conversation_capture_input_state",
        ),
        CheckConstraint(
            "(processing_grant_id IS NULL AND processing_policy_version IS NULL) OR "
            "(processing_grant_id IS NOT NULL AND processing_policy_version IS NOT NULL)",
            name="ck_conversation_capture_inputs_processing_provenance",
        ),
        UniqueConstraint("owner_id", "input_key", name="uq_conversation_capture_input_key"),
        ForeignKeyConstraint(
            ["draft_id", "owner_id"],
            ["conversation.capture_drafts.id", "conversation.capture_drafts.owner_id"],
            ondelete="CASCADE",
            name="fk_conversation_capture_input_draft_owner",
        ),
        Index("ix_conversation_capture_input_draft", "draft_id", "sequence"),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    processing_grant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    processing_policy_version: Mapped[str | None] = mapped_column(String(64))
    draft_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    input_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    response: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaptureConfirmationRecord(Base):
    __tablename__ = "capture_confirmations"
    __table_args__ = (
        CheckConstraint("preview_version >= 1", name="ck_conversation_capture_proof_version"),
        CheckConstraint(
            "length(projection_sha256) = 64", name="ck_conversation_capture_proof_digest"
        ),
        CheckConstraint(
            "length(credential_sha256) = 64",
            name="ck_conversation_capture_credential_digest",
        ),
        UniqueConstraint(
            "owner_id",
            "draft_id",
            "preview_version",
            "part_key",
            name="uq_conversation_capture_proof",
        ),
        ForeignKeyConstraint(
            ["draft_id", "owner_id"],
            ["conversation.capture_drafts.id", "conversation.capture_drafts.owner_id"],
            ondelete="CASCADE",
            name="fk_conversation_capture_proof_draft_owner",
        ),
        Index("ix_conversation_capture_proof_expiry", "expires_at"),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    draft_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    preview_version: Mapped[int] = mapped_column(Integer, nullable=False)
    part_key: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
