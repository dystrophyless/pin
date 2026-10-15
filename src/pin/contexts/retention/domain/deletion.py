"""Owner-confirmed deletion lifecycle values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

DELETION_PREVIEW_TTL_SECONDS = 24 * 60 * 60
TEXT_RETENTION_DAYS = 30


class DeletionState(StrEnum):
    PREVIEWED = "previewed"
    CANCELLED = "cancelled"
    PENDING_EXPORT = "pending_export"
    COMPLETED = "completed"


class DeletionRejected(ValueError):
    """A deletion preview is stale, foreign, expired, or the account is unavailable."""


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    id: UUID
    owner_id: UUID
    version: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeletionTombstone:
    event_id: UUID
    owner_id: UUID
    deleted_at: datetime
