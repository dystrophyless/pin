"""Platform-owned SQLAlchemy records for durable work and event delivery."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class InboxUpdate(Base):
    __tablename__ = "inbox"
    __table_args__ = (
        UniqueConstraint("bot_id", "update_id", name="uq_platform_inbox_bot_update"),
        CheckConstraint(
            "(processing_grant_id IS NULL AND processing_policy_version IS NULL) OR "
            "(processing_grant_id IS NOT NULL AND processing_policy_version IS NOT NULL)",
            name="ck_platform_inbox_processing_provenance",
        ),
        {"schema": "platform"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    processing_grant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    processing_policy_version: Mapped[str | None] = mapped_column(String(64))
    update_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("lane", "deduplication_key", name="uq_platform_jobs_lane_dedupe"),
        CheckConstraint(
            "lane IN ('application', 'speech', 'delivery')", name="ck_platform_job_lane"
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'succeeded', 'dead')", name="ck_platform_job_status"
        ),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="ck_platform_job_attempts"),
        CheckConstraint(
            "conversation_sequence IS NULL OR (owner_id IS NOT NULL AND conversation_sequence > 0)",
            name="ck_platform_job_conversation_sequence",
        ),
        CheckConstraint(
            "(processing_grant_id IS NULL AND processing_policy_version IS NULL) OR "
            "(processing_grant_id IS NOT NULL AND processing_policy_version IS NOT NULL)",
            name="ck_platform_jobs_processing_provenance",
        ),
        Index("ix_platform_jobs_claim", "lane", "status", "available_at", "priority"),
        Index("ix_platform_jobs_owner_order", "owner_id", "conversation_sequence", "status"),
        {"schema": "platform"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    lane: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(180), nullable=False)
    owner_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    processing_grant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    processing_policy_version: Mapped[str | None] = mapped_column(String(64))
    conversation_sequence: Mapped[int | None] = mapped_column(BigInteger)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    worker_id: Mapped[str | None] = mapped_column(String(120))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    """Latest liveness record for one worker process; payloads never enter this table."""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        Index("ix_platform_worker_heartbeats_role_seen", "role", "last_seen_at"),
        CheckConstraint(
            "role IN ('application', 'delivery', 'speech', 'gateway')",
            name="ck_platform_worker_heartbeat_role",
        ),
        {"schema": "platform"},
    )

    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationCursor(Base):
    __tablename__ = "conversation_cursors"
    __table_args__ = {"schema": "platform"}

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    next_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class ConversationLease(Base):
    __tablename__ = "conversation_leases"
    __table_args__ = (
        CheckConstraint("fencing_generation >= 1", name="ck_platform_fencing_generation"),
        {"schema": "platform"},
    )

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    job_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    fencing_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint("length(event_type) BETWEEN 1 AND 120", name="ck_platform_event_type"),
        CheckConstraint(
            "schema_version >= 1 AND aggregate_version >= 1", name="ck_platform_event_version"
        ),
        Index("ix_platform_outbox_pending", "published_at", "created_at"),
        Index("ix_platform_outbox_aggregate", "aggregate_id", "aggregate_version"),
        {"schema": "platform"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    causation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxConsumption(Base):
    __tablename__ = "outbox_consumptions"
    __table_args__ = (
        UniqueConstraint("consumer_name", "event_id", name="uq_platform_consumer_event"),
        Index(
            "uq_platform_consumer_version",
            "consumer_name",
            "aggregate_id",
            "aggregate_version",
            unique=True,
            postgresql_where=text("status <> 'stale'"),
        ),
        CheckConstraint(
            "status IN ('pending', 'applied', 'stale')", name="ck_platform_consumption_status"
        ),
        Index(
            "ix_platform_consumption_reconcile",
            "consumer_name",
            "aggregate_id",
            "status",
            "aggregate_version",
        ),
        {"schema": "platform"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    consumer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))


class ConsumerCursor(Base):
    __tablename__ = "consumer_cursors"
    __table_args__ = {"schema": "platform"}

    consumer_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    last_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class RecoveryGate(Base):
    """Singleton startup fence and evidence references for restored snapshots."""

    __tablename__ = "recovery_gate"
    __table_args__ = (
        CheckConstraint("singleton = true", name="ck_platform_recovery_gate_singleton"),
        CheckConstraint("state IN ('ready', 'fenced')", name="ck_platform_recovery_gate_state"),
        CheckConstraint(
            "(snapshot_id IS NULL AND ledger_id IS NULL AND snapshot_at IS NULL "
            "AND state = 'ready') OR "
            "(snapshot_id IS NOT NULL AND ledger_id IS NOT NULL AND snapshot_at IS NOT NULL "
            "AND ledger_id > snapshot_id)",
            name="ck_platform_recovery_gate_artifact_order",
        ),
        {"schema": "platform"},
    )

    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(32))
    ledger_id: Mapped[str | None] = mapped_column(String(32))
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fenced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    delivery_evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    payment_evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    usage_evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    consent_invalidated_snapshot_id: Mapped[str | None] = mapped_column(String(32))
    consent_invalidated_ledger_id: Mapped[str | None] = mapped_column(String(32))
