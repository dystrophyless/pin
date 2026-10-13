"""Versioned events shared across bounded-context contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    id: UUID
    event_type: str
    schema_version: int
    aggregate_id: UUID
    aggregate_version: int
    owner_id: UUID
    occurred_at: datetime
    payload: dict[str, Any]
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.event_type.strip() or len(self.event_type) > 120:
            raise ValueError("event_type must contain 1 to 120 characters")
        if self.schema_version < 1 or self.aggregate_version < 1:
            raise ValueError("event versions must be positive")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "aggregate_id": str(self.aggregate_id),
            "aggregate_version": self.aggregate_version,
            "owner_id": str(self.owner_id),
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventEnvelope:
        return cls(
            id=UUID(data["id"]),
            event_type=data["event_type"],
            schema_version=int(data["schema_version"]),
            aggregate_id=UUID(data["aggregate_id"]),
            aggregate_version=int(data["aggregate_version"]),
            owner_id=UUID(data["owner_id"]),
            occurred_at=datetime.fromisoformat(data["occurred_at"]),
            correlation_id=UUID(data["correlation_id"]) if data["correlation_id"] else None,
            causation_id=UUID(data["causation_id"]) if data["causation_id"] else None,
            payload=data["payload"],
        )
