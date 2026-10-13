"""Identity-owned PostgreSQL records."""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Time, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class PreferencesRecord(Base):
    __tablename__ = "preferences"
    __table_args__ = (
        CheckConstraint("language IN ('ru', 'en')", name="ck_identity_language"),
        CheckConstraint("version >= 1", name="ck_identity_preferences_version"),
        CheckConstraint(
            "reminder_calendar_days >= 0 AND reminder_elapsed_hours >= 0",
            name="ck_identity_reminder_offsets",
        ),
        CheckConstraint("quiet_start <> quiet_end", name="ck_identity_quiet_interval"),
        {"schema": "identity"},
    )

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    home_zone: Mapped[str] = mapped_column(String(128), nullable=False)
    reminder_calendar_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reminder_elapsed_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quiet_start: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    quiet_end: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    review_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OnboardingRecord(Base):
    __tablename__ = "onboarding"
    __table_args__ = (
        CheckConstraint("mode IN ('initial', 'preferences')", name="ck_identity_onboarding_mode"),
        CheckConstraint(
            "step IN ('language', 'home_zone', 'reminders', 'quiet_review', 'confirm', 'complete')",
            name="ck_identity_onboarding_step",
        ),
        CheckConstraint("version >= 1", name="ck_identity_onboarding_version"),
        {"schema": "identity"},
    )

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(20), nullable=False)
    draft: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    base_profile_version: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
