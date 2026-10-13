"""Speech-owned durable transcript state for retry-safe continuation."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Float, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class SpeechRequestRecord(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(
            "state IN ('processing', 'transcribed', 'continued', 'failed', 'rejected')",
            name="ck_speech_request_state",
        ),
        CheckConstraint(
            "(state = 'transcribed' AND transcript IS NOT NULL AND language IS NOT NULL) OR "
            "(state <> 'transcribed' AND transcript IS NULL)",
            name="ck_speech_request_transcript_state",
        ),
        CheckConstraint(
            "declared_size_bytes IS NULL OR declared_size_bytes > 0",
            name="ck_speech_declared_size_positive",
        ),
        CheckConstraint(
            "declared_duration_seconds > 0", name="ck_speech_declared_duration_positive"
        ),
        CheckConstraint(
            "actual_size_bytes IS NULL OR actual_size_bytes BETWEEN 1 AND 10000000",
            name="ck_speech_actual_size_bounds",
        ),
        CheckConstraint(
            "decoded_duration_seconds IS NULL OR decoded_duration_seconds BETWEEN 0.000001 AND 180",
            name="ck_speech_decoded_duration_bounds",
        ),
        CheckConstraint("language IS NULL OR language IN ('ru', 'en')", name="ck_speech_language"),
        Index("ix_speech_requests_owner_created", "owner_id", "created_at"),
        {"schema": "speech"},
    )

    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    inbox_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    request_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    declared_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    declared_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    actual_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    decoded_duration_seconds: Mapped[float | None] = mapped_column(Float)
    transcript: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(2))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
