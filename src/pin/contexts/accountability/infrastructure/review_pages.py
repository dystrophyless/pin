"""Current, owner-bound read pages for large daily reviews."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.accountability.application.contracts import (
    DailyReviewPage,
    OpenUntimedItem,
    TodayUntimedReader,
)
from pin.contexts.accountability.domain.reviews import ReviewItem, review_items_for_today
from pin.contexts.accountability.infrastructure.models import DailyReviewRecord

REVIEW_PAGE_SIZE = 10


async def get_daily_review_page(
    session: AsyncSession,
    *,
    owner_id: UUID,
    review_id: UUID,
    page_number: int,
    today_untimed: TodayUntimedReader,
    now: datetime,
) -> DailyReviewPage | None:
    """Page only current today-open items captured by this still-current review."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("review page clock must be timezone-aware")
    if page_number < 0:
        raise ValueError("review page number must not be negative")
    review = await session.scalar(
        select(DailyReviewRecord).where(
            DailyReviewRecord.id == review_id,
            DailyReviewRecord.owner_id == owner_id,
        )
    )
    if review is None or review.state != "open":
        return None
    at = now.astimezone(UTC)
    today = at.astimezone(ZoneInfo(review.zone_name)).date()
    if review.review_date != today:
        return None
    items = await today_untimed.list_today_open_untimed(
        owner_id,
        profile_zone=review.zone_name,
        at=now,
    )
    current_by_id = {item.occurrence_id: item for item in items if item.local_date == today}
    ordered = review_items_for_today(
        tuple(
            ReviewItem(
                current_by_id[occurrence_id].occurrence_id, current_by_id[occurrence_id].title
            )
            for occurrence_id in (UUID(value) for value in review.occurrence_ids)
            if occurrence_id in current_by_id
        )
    )
    if not ordered:
        return None
    page_count = (len(ordered) + REVIEW_PAGE_SIZE - 1) // REVIEW_PAGE_SIZE
    if page_number >= page_count:
        return None
    start = page_number * REVIEW_PAGE_SIZE
    page_items = tuple(
        OpenUntimedItem(item.occurrence_id, item.title, today)
        for item in ordered[start : start + REVIEW_PAGE_SIZE]
    )
    return DailyReviewPage(
        review_id=review.id,
        owner_id=review.owner_id,
        language=review.language,
        page_number=page_number,
        page_count=page_count,
        total_items=len(ordered),
        items=page_items,
    )
