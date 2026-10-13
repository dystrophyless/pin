"""Conversation-owned provider cost receipts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class ProviderCallRecord(Base):
    """One reserved provider call; unresolved calls retain unknown usage as null."""

    __tablename__ = "provider_calls"
    __table_args__ = (
        UniqueConstraint("owner_id", "turn_id", "call_id", name="uq_model_call_identity"),
        UniqueConstraint("provider_request_id", name="uq_model_provider_request"),
        CheckConstraint("call_number BETWEEN 1 AND 4", name="ck_model_call_number"),
        CheckConstraint(
            "state IN ('started', 'unknown', 'reported', 'deduplicated')",
            name="ck_model_call_state",
        ),
        CheckConstraint(
            "(state = 'reported' AND prompt_tokens IS NOT NULL "
            "AND completion_tokens IS NOT NULL AND total_tokens IS NOT NULL) OR "
            "state = 'unknown' OR "
            "(state IN ('started', 'deduplicated') AND prompt_tokens IS NULL "
            "AND completion_tokens IS NULL AND total_tokens IS NULL AND cost_usd IS NULL)",
            name="ck_model_usage_presence",
        ),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0", name="ck_model_prompt_tokens"
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_model_completion_tokens",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR prompt_tokens IS NULL OR completion_tokens IS NULL "
            "OR total_tokens = prompt_tokens + completion_tokens",
            name="ck_model_total_tokens",
        ),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="ck_model_cost_nonnegative"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_model_latency"),
        CheckConstraint(
            "(state = 'deduplicated' AND duplicate_of_call_id IS NOT NULL) OR "
            "(state <> 'deduplicated' AND duplicate_of_call_id IS NULL)",
            name="ck_model_duplicate_link",
        ),
        Index("ix_model_calls_owner_turn", "owner_id", "turn_id", "call_number"),
        Index("ix_model_calls_state_created", "state", "created_at"),
        {"schema": "conversation"},
    )

    call_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    call_number: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="started")
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    duplicate_of_call_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    result_category: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationTurnResultRecord(Base):
    """Validated result retained so duplicate delivery can resume after a crash."""

    __tablename__ = "turn_results"
    __table_args__ = (
        UniqueConstraint("owner_id", "turn_id", name="uq_conversation_turn_result"),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    provider_request_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
