"""Telegram action tokens and localized messages for Accountability deliveries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.mutation_runtime import MutationMessage, process_mutation_command
from pin.adapters.telegram.presentation.callbacks import CallbackTokens, InvalidCallbackToken
from pin.adapters.telegram.presentation.tokens import SqlAlchemyCallbackTokenStore
from pin.contexts.accountability.api import (
    AccountabilitySend,
    CurrentOccurrence,
    DailyReviewPage,
    TodayUntimedReader,
    get_daily_review_page,
)

ACCOUNTABILITY_CALLBACK_PREFIX = "ac:"
_ACTION_LABELS = {
    "en": {"done": "Done", "cancel": "Cancel", "reschedule": "Reschedule"},
    "ru": {"done": "Готово", "cancel": "Отменить", "reschedule": "Перенести"},
}
_REVIEW_PAGE_SIZE = 10
_REVIEW_PAGE_ACTION = "accountability.review.page"


async def render_accountability_delivery(
    session: AsyncSession,
    send: AccountabilitySend,
    *,
    occurrences: tuple[CurrentOccurrence, ...],
    now: datetime,
) -> tuple[str, dict[str, object]]:
    """Render a message and issue ordinary owner-bound mutation action tokens."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if send.language not in _ACTION_LABELS:
        raise ValueError("accountability language must be en or ru")
    current = {item.occurrence_id: item for item in occurrences}
    visible = tuple(
        current[occurrence_id]
        for occurrence_id in send.occurrence_ids
        if occurrence_id in current
        and current[occurrence_id].owner_id == send.owner_id
        and current[occurrence_id].state == "open"
    )
    if send.review_id is not None:
        page_count = max(1, (len(visible) + _REVIEW_PAGE_SIZE - 1) // _REVIEW_PAGE_SIZE)
        selected = visible[:_REVIEW_PAGE_SIZE]
        text = _review_page_text(
            send.language,
            send.slot.value,
            tuple(item.title for item in selected),
            page_number=0,
            page_count=page_count,
            total_items=len(visible),
        )
        keyboard = await _review_page_keyboard(
            session,
            owner_id=send.owner_id,
            review_id=send.review_id,
            page_number=0,
            page_count=page_count,
            items=tuple((item.occurrence_id, item.title) for item in selected),
            language=send.language,
            now=now,
        )
        return text, keyboard
    rows = await _item_action_rows(
        session,
        owner_id=send.owner_id,
        items=tuple((item.occurrence_id, item.title) for item in visible),
        language=send.language,
        now=now,
    )
    return send.plain_text, {"inline_keyboard": rows}


async def process_accountability_action(
    session: AsyncSession,
    *,
    owner_id: UUID,
    data: str | None,
    language: str,
    now: datetime,
    today_untimed: TodayUntimedReader | None = None,
) -> MutationMessage | None:
    """Turn a review action into the existing preview/confirmation flow."""
    if not data or not data.startswith(ACCOUNTABILITY_CALLBACK_PREFIX):
        return None
    if len(data.encode("utf-8")) > 64:
        return MutationMessage(
            "This action is invalid or expired." if language == "en" else "Действие устарело."
        )
    token = data.removeprefix(ACCOUNTABILITY_CALLBACK_PREFIX)
    if not token:
        return None
    try:
        binding = await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).consume(
            token,
            actor_id=owner_id,
            current_target_versions={
                "accountability.done": 0,
                "accountability.cancel": 0,
                "accountability.reschedule": 0,
                _REVIEW_PAGE_ACTION: 0,
            },
            now=now,
        )
    except InvalidCallbackToken:
        return MutationMessage(
            "This action is invalid or expired." if language == "en" else "Действие устарело."
        )
    if binding.action == _REVIEW_PAGE_ACTION:
        if today_untimed is None:
            return _stale_review_page(language)
        try:
            raw_review, raw_page = binding.target_id.rsplit(":", 1)
            review_id = UUID(raw_review)
            page_number = int(raw_page)
        except (ValueError, TypeError):
            return _stale_review_page(language)
        page = await get_daily_review_page(
            session,
            owner_id=owner_id,
            review_id=review_id,
            page_number=page_number,
            today_untimed=today_untimed,
            now=now,
        )
        if page is None:
            return _stale_review_page(language)
        keyboard = await render_accountability_review_page(session, page, now=now)
        titles = tuple(item.title for item in page.items)
        text = _review_page_text(
            page.language,
            "initial",
            titles,
            page_number=page.page_number,
            page_count=page.page_count,
            total_items=page.total_items,
        )
        return MutationMessage(text, keyboard)

    action = binding.action.removeprefix("accountability.")
    if action not in {"done", "cancel", "reschedule"}:
        return None
    command = f"/{action} {binding.target_id}"
    return await process_mutation_command(
        session,
        owner_id=owner_id,
        text=command,
        language=language,
        now=now,
    )


async def render_accountability_review_page(
    session: AsyncSession,
    page: DailyReviewPage,
    *,
    now: datetime,
) -> dict[str, object]:
    """Create bounded row actions and navigators for one review page."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return await _review_page_keyboard(
        session,
        owner_id=page.owner_id,
        review_id=page.review_id,
        page_number=page.page_number,
        page_count=page.page_count,
        items=tuple((item.occurrence_id, item.title) for item in page.items),
        language=page.language,
        now=now,
    )


async def _review_page_keyboard(
    session: AsyncSession,
    *,
    owner_id: UUID,
    review_id: UUID,
    page_number: int,
    page_count: int,
    items: tuple[tuple[UUID, str], ...],
    language: str,
    now: datetime,
) -> dict[str, object]:
    rows = await _item_action_rows(
        session, owner_id=owner_id, items=items, language=language, now=now
    )
    tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    expires_at = now.astimezone(UTC) + timedelta(minutes=15)
    navigation: list[dict[str, str]] = []
    for target_page, label in (
        (page_number - 1, "‹ Previous" if language == "en" else "‹ Назад"),
        (page_number + 1, "Next ›" if language == "en" else "Далее ›"),
    ):
        if not 0 <= target_page < page_count:
            continue
        token = await tokens.issue(
            owner_id=owner_id,
            action=_REVIEW_PAGE_ACTION,
            target_id=f"{review_id}:{target_page}",
            target_version=0,
            expires_at=expires_at,
            now=now.astimezone(UTC),
        )
        navigation.append(
            {
                "text": label,
                "callback_data": f"{ACCOUNTABILITY_CALLBACK_PREFIX}{token}",
            }
        )
    if navigation:
        rows.append(navigation)
    return {"inline_keyboard": rows}


async def _item_action_rows(
    session: AsyncSession,
    *,
    owner_id: UUID,
    items: tuple[tuple[UUID, str], ...],
    language: str,
    now: datetime,
) -> list[list[dict[str, str]]]:
    tokens = CallbackTokens(SqlAlchemyCallbackTokenStore(session))
    expires_at = now.astimezone(UTC) + timedelta(minutes=15)
    labels = _ACTION_LABELS[language]
    rows: list[list[dict[str, str]]] = []
    for occurrence_id, _title in items[:_REVIEW_PAGE_SIZE]:
        row: list[dict[str, str]] = []
        for action in ("done", "cancel", "reschedule"):
            token = await tokens.issue(
                owner_id=owner_id,
                action=f"accountability.{action}",
                target_id=str(occurrence_id),
                target_version=0,
                expires_at=expires_at,
                now=now.astimezone(UTC),
            )
            row.append(
                {
                    "text": labels[action],
                    "callback_data": f"{ACCOUNTABILITY_CALLBACK_PREFIX}{token}",
                }
            )
        rows.append(row)
    return rows


def _review_page_text(
    language: str,
    slot: str,
    titles: tuple[str, ...],
    *,
    page_number: int,
    page_count: int,
    total_items: int,
) -> str:
    if language == "ru":
        heading = (
            "Удалось что-нибудь завершить?" if slot == "nudge" else "Что из этого удалось сделать?"
        )
        page = f"Страница {page_number + 1} из {page_count} · всего {total_items}"
    else:
        heading = (
            "Did you manage to finish any of these?"
            if slot == "nudge"
            else "Which of these did you complete?"
        )
        page = f"Page {page_number + 1} of {page_count} · {total_items} items"
    return "\n".join((heading, page, *(f"• {title}" for title in titles)))[:3500]


def _stale_review_page(language: str) -> MutationMessage:
    return MutationMessage(
        "This review page is no longer current."
        if language == "en"
        else "Эта страница обзора устарела."
    )


def render_checkin_text(send: AccountabilitySend) -> str:
    title = (send.title or "").strip()
    if send.language == "ru":
        if send.slot.value == "nudge":
            return f"Удалось завершить «{title}»?"
        return f"Вы завершили «{title}»?"
    if send.slot.value == "nudge":
        return f"Did you manage to finish “{title}”?"
    return f"Did you complete “{title}”?"


def render_review_text(send: AccountabilitySend, items: tuple[str, ...]) -> str:
    visible = tuple(item.strip() for item in items if item.strip())
    if not visible:
        raise ValueError("review delivery needs at least one current item")
    if send.language == "ru":
        heading = (
            "Что из этого удалось сделать?"
            if send.slot.value == "initial"
            else "Удалось что-нибудь завершить?"
        )
        lines = [heading, *(f"• {title}" for title in visible[:10])]
        if len(visible) > 10:
            lines.append(f"И ещё {len(visible) - 10}.")
    elif send.language == "en":
        heading = (
            "Which of these did you complete?"
            if send.slot.value == "initial"
            else "Did you manage to finish any of these?"
        )
        lines = [heading, *(f"• {title}" for title in visible[:10])]
        if len(visible) > 10:
            lines.append(f"And {len(visible) - 10} more.")
    else:
        raise ValueError("accountability language must be en or ru")
    return "\n".join(lines)[:3500]


__all__ = [
    "ACCOUNTABILITY_CALLBACK_PREFIX",
    "process_accountability_action",
    "render_accountability_review_page",
    "render_accountability_delivery",
    "render_checkin_text",
    "render_review_text",
]
