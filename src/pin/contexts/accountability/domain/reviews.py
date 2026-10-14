"""Stable, owner-scoped grouping and response rules for one evening review."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ReviewItem:
    occurrence_id: UUID
    title: str

    def __post_init__(self) -> None:
        if self.occurrence_id.int == 0 or not self.title.strip():
            raise ValueError("review item requires an occurrence and title")


def review_items_for_today(items: tuple[ReviewItem, ...]) -> tuple[ReviewItem, ...]:
    """Deduplicate but preserve Planning's stable display order."""
    seen: set[UUID] = set()
    result: list[ReviewItem] = []
    for item in items:
        if item.occurrence_id not in seen:
            seen.add(item.occurrence_id)
            result.append(item)
    return tuple(result)
