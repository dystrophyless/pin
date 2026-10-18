from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from pin.adapters.telegram.presentation.callbacks import (
    CallbackBinding,
    CallbackTokens,
    InvalidCallbackToken,
)
from pin.adapters.telegram.presentation.delivery import send_formatted_message, typing_status
from pin.adapters.telegram.presentation.formatting import (
    MessageEntry,
    escape_markdown_v2,
    paginate_entries,
    telegram_utf16_length,
)
from pin.adapters.telegram.presentation.mutations import render_mutation_preview
from pin.adapters.telegram.presentation.onboarding import render_onboarding
from pin.adapters.telegram.presentation.router import create_help_support_router
from pin.adapters.telegram.presentation.templates import render_help, render_support
from pin.contexts.identity.application.onboarding import OnboardingResult
from pin.contexts.planning.domain.mutations import (
    MutationAction,
    MutationPreview,
    MutationRequest,
    MutationScope,
    MutationSeriesProjection,
    MutationTarget,
)
from pin.contexts.planning.domain.plans import (
    OccurrenceState,
    OneOffPlanDraft,
    PlanKind,
    resolve_schedule,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceExceptionScope,
    RecurrenceFrequency,
    RecurrenceProjection,
    RecurrenceRule,
    RecurringPlanDraft,
)


class MemoryCallbackStore:
    def __init__(self) -> None:
        self.bindings: dict[str, CallbackBinding] = {}
        self.consumed: set[str] = set()

    async def issue(self, binding: CallbackBinding) -> None:
        self.bindings[binding.token_hash] = binding

    async def consume(
        self, token_hash: str, owner_id: UUID, now: datetime
    ) -> CallbackBinding | None:
        binding = self.bindings.get(token_hash)
        if (
            binding is None
            or binding.owner_id != owner_id
            or binding.expires_at <= now
            or token_hash in self.consumed
        ):
            return None
        self.consumed.add(token_hash)
        return binding


def _weekly_mutation_preview(*, end_date: date | None) -> MutationPreview:
    owner_id = uuid4()
    series_id = uuid4()
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    original = OneOffPlanDraft(
        PlanKind.TASK, "Weekly review", date(2026, 10, 21), time(12), "UTC"
    )
    replacement = OneOffPlanDraft(
        PlanKind.TASK, "Weekly review", date(2026, 10, 28), time(12), "UTC"
    )
    rule = RecurrenceRule(
        RecurrenceFrequency.WEEKLY, weekdays=(2,), end_date=end_date
    )
    draft = RecurringPlanDraft(
        PlanKind.TASK,
        "Weekly review",
        date(2026, 10, 21),
        time(12),
        "UTC",
        rule,
    )
    original_schedule = resolve_schedule(original, default_zone="UTC", now=now)
    replacement_schedule = resolve_schedule(replacement, default_zone="UTC", now=now)
    target = MutationTarget(
        owner_id,
        uuid4(),
        uuid4(),
        "Weekly review",
        1,
        1,
        1,
        OccurrenceState.OPEN,
        original_schedule,
        recurrence_series_id=series_id,
        recurrence_sequence=0,
    )
    request = MutationRequest(
        owner_id,
        target.occurrence_id,
        1,
        MutationAction.RESCHEDULE,
        MutationScope.THIS_AND_FUTURE,
        replacement,
    )
    change = RecurrenceExceptionDraft(
        RecurrenceExceptionScope.THIS_AND_FUTURE, 0, replacement, new_pattern=rule
    )
    series = MutationSeriesProjection(
        1,
        change,
        draft,
        RecurrenceProjection((), (), False, "test"),
        end_date or date(2027, 1, 1),
        None,
        "test",
    )
    return MutationPreview(
        uuid4(),
        owner_id,
        target,
        request,
        replacement_schedule,
        series,
        None,
        None,
        (replacement_schedule,),
        1,
        1,
        now,
        now + timedelta(hours=1),
        "test",
    )


@pytest.mark.parametrize(
    ("language", "end_date", "expected", "absent"),
    [
        ("ru", date(2026, 12, 31), "до 2026-12-31", "не ограничено"),
        ("en", date(2026, 12, 31), "through 2026-12-31", "unlimited"),
        ("ru", None, "количество оставшихся повторений не ограничено", "до 2026-12-31"),
        ("en", None, "unlimited remaining occurrences", "through"),
    ],
)
def test_mutation_weekly_preview_distinguishes_end_date_from_unbounded(
    language: str, end_date: date | None, expected: str, absent: str
) -> None:
    _, detail, _, _ = render_mutation_preview(
        _weekly_mutation_preview(end_date=end_date), language
    )

    assert expected in detail
    assert absent not in detail


def test_markdown_v2_escapes_all_reserved_characters_and_preserves_unicode() -> None:
    specials = r"_*[]()~`>#+-=|{}.!" + chr(92)
    value = f"Привет {specials} 😀 e\u0301"
    escaped = escape_markdown_v2(value)
    assert escaped == "Привет " + "".join(f"\\{char}" for char in specials) + " 😀 e\u0301"


def test_onboarding_markdown_keeps_code_spans_and_ru_buttons_are_localized() -> None:
    language = render_onboarding(
        OnboardingResult("started", step="language", mode="initial", draft={"language": "ru"})
    )
    assert language is not None
    assert language.markdown == r"Выберите язык: отправьте `ru` или `en`\."
    assert language.plain_text == "Выберите язык: отправьте ru или en."
    assert {button.text for button in language.buttons} >= {"English", "Русский", "Отмена"}

    reminders = render_onboarding(
        OnboardingResult("started", step="reminders", mode="initial", draft={"language": "ru"})
    )
    assert reminders is not None
    assert "suggested" not in reminders.plain_text.casefold()
    assert any(button.text == "Использовать предложенное" for button in reminders.buttons)
    zone = render_onboarding(
        OnboardingResult("started", step="home_zone", mode="initial", draft={"language": "ru"})
    )
    assert zone is not None and "Asia/Almaty (пример)" in {button.text for button in zone.buttons}
    quiet = render_onboarding(
        OnboardingResult("started", step="quiet_review", mode="initial", draft={"language": "ru"})
    )
    assert quiet is not None
    assert "`22:00-08:00; 20:00`" in quiet.markdown
    assert "22:00-08:00; 20:00" in quiet.plain_text

    confirmation = render_onboarding(
        OnboardingResult(
            "advanced",
            step="confirm",
            mode="initial",
            draft={"language": "ru", "home_zone": "Asia/Almaty"},
        )
    )
    assert confirmation is not None
    assert "*Проверьте настройки:*" in confirmation.markdown
    assert "**" not in confirmation.plain_text


def test_onboarding_cancel_copy_matches_initial_and_preview_states() -> None:
    initial = render_onboarding(OnboardingResult("cancelled", mode="initial"))
    preview = render_onboarding(
        OnboardingResult("cancelled", mode="preferences", draft={"language": "en"})
    )
    assert initial is not None and "no trial was started" in initial.plain_text
    assert (
        preview is not None
        and "saved preferences and access are unchanged" in preview.plain_text.casefold()
    )
    assert "no trial was started" not in preview.plain_text


def test_pagination_keeps_unicode_and_button_targets_on_safe_pages() -> None:
    entries = [
        MessageEntry(f"пункт {index} 😀 " + "_*[x]" * 80, target_id=f"target-{index}")
        for index in range(70)
    ]
    pages = paginate_entries("Планы", entries, empty_text="Нет планов", limit=420)
    assert len(pages) > 1
    assert all(telegram_utf16_length(page.plain_text) <= 420 for page in pages)
    assert [target for page in pages for target in page.target_ids] == [
        f"target-{index}" for index in range(70)
    ]
    assert all(page.markdown.startswith("*Планы*\n") for page in pages)
    assert "😀" in "".join(page.plain_text for page in pages)


def test_long_unicode_entry_is_split_without_loss_or_oversize() -> None:
    long_text = ("Ж😀e\u0301_*" * 1500) + " end"
    pages = paginate_entries("Текст", [MessageEntry(long_text)], empty_text="empty", limit=512)
    recovered = "".join(line[2:] for page in pages for line in page.plain_text.splitlines()[1:])
    assert recovered == long_text
    assert all(telegram_utf16_length(page.plain_text) <= 512 for page in pages)


def test_emoji_pagination_uses_utf16_units_and_never_splits_surrogate_pairs() -> None:
    value = "😀" * 2047
    pages = paginate_entries(
        "X", [MessageEntry(value, target_id="emoji-target")], empty_text="empty"
    )
    assert len(pages) == 2
    assert all(telegram_utf16_length(page.plain_text) <= 4096 for page in pages)
    recovered = "".join(line[2:] for page in pages for line in page.plain_text.splitlines()[1:])
    assert recovered == value
    assert [target for page in pages for target in page.target_ids] == ["emoji-target"]


@pytest.mark.asyncio
async def test_callback_tokens_bind_owner_version_expiry_and_are_single_use() -> None:
    now = datetime(2026, 10, 13, tzinfo=UTC)
    store = MemoryCallbackStore()
    tokens = CallbackTokens(store)
    token = await tokens.issue(
        owner_id=UUID(int=17),
        action="complete",
        target_id="occurrence-1",
        target_version=3,
        expires_at=now + timedelta(minutes=5),
        now=now,
    )
    assert len(token.encode("utf-8")) <= 64

    with pytest.raises(InvalidCallbackToken):
        await tokens.consume(token, actor_id=UUID(int=18), current_target_version=3, now=now)
    with pytest.raises(InvalidCallbackToken):
        await tokens.consume(token, actor_id=UUID(int=17), current_target_version=4, now=now)
    with pytest.raises(InvalidCallbackToken):
        await tokens.consume(token, actor_id=UUID(int=17), current_target_version=3, now=now)

    valid = await tokens.issue(
        owner_id=UUID(int=17),
        action="complete",
        target_id="occurrence-2",
        target_version=5,
        expires_at=now + timedelta(minutes=5),
        now=now,
    )
    binding = await tokens.consume(valid, actor_id=UUID(int=17), current_target_version=5, now=now)
    assert (binding.action, binding.target_id, binding.target_version) == (
        "complete",
        "occurrence-2",
        5,
    )
    with pytest.raises(InvalidCallbackToken):
        await tokens.consume(valid, actor_id=UUID(int=17), current_target_version=5, now=now)

    fresh = await tokens.issue(
        owner_id=UUID(int=17),
        action="reschedule",
        target_id="occurrence-1",
        target_version=3,
        expires_at=now + timedelta(minutes=5),
        now=now,
    )
    with pytest.raises(InvalidCallbackToken):
        await tokens.consume(
            fresh,
            actor_id=UUID(int=17),
            current_target_version=3,
            now=now + timedelta(minutes=5),
        )

    with pytest.raises(ValueError, match="within 15 minutes"):
        await tokens.issue(
            owner_id=UUID(int=17),
            action="complete",
            target_id="occurrence-1",
            target_version=3,
            expires_at=now + timedelta(minutes=16),
            now=now,
        )


def test_help_and_support_are_localized_and_missing_destination_fails_closed() -> None:
    assert "/today" in render_help("en")
    assert "/today" in render_help("ru")
    assert "\u0441\u0435\u0433\u043e\u0434\u043d\u044f" in render_help("ru").casefold()
    assert "\\_" in render_support("en", "@team_name")
    with pytest.raises(ValueError, match="not configured"):
        render_support("ru", None)
    with pytest.raises(ValueError, match="not configured"):
        create_help_support_router(
            support_contact=None,
            language_for_telegram_user=lambda _: asyncio.sleep(0, result="en"),
        )


def test_help_support_router_registers_only_the_owned_private_commands() -> None:
    router = create_help_support_router(
        support_contact="@pin_support",
        language_for_telegram_user=lambda _: asyncio.sleep(0, result="en"),
    )
    assert isinstance(router, Router)
    assert len(router.message.handlers) == 2


class FakeBot:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.actions = 0

    async def send_message(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if self.error is not None and len(self.calls) == 1:
            raise self.error
        return "sent"

    async def send_chat_action(self, **_: object) -> None:
        self.actions += 1


@pytest.mark.asyncio
async def test_only_confirmed_format_rejection_retries_as_plain_text() -> None:
    bot = FakeBot(TelegramBadRequest(SendMessage(chat_id=1, text="x"), "can't parse entities"))
    await send_formatted_message(
        bot,
        chat_id=1,
        markdown="*x",
        plain_text="x",  # type: ignore[arg-type]
    )
    assert [call["parse_mode"] for call in bot.calls] == [ParseMode.MARKDOWN_V2, None]


@pytest.mark.asyncio
async def test_unknown_send_failure_is_not_retried() -> None:
    bot = FakeBot(TimeoutError("outcome unknown"))
    with pytest.raises(TimeoutError, match="unknown"):
        await send_formatted_message(bot, chat_id=1, markdown="*x*", plain_text="x")  # type: ignore[arg-type]
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_non_format_telegram_error_is_not_retried() -> None:
    bot = FakeBot(TelegramBadRequest(SendMessage(chat_id=1, text="x"), "chat not found"))
    with pytest.raises(TelegramBadRequest):
        await send_formatted_message(
            bot,
            chat_id=1,
            markdown="*x*",
            plain_text="x",  # type: ignore[arg-type]
        )
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_typing_status_runs_during_work_and_stops_on_exit() -> None:
    bot = FakeBot()
    started = asyncio.Event()
    original = bot.send_chat_action

    async def observed_action(**kwargs: object) -> None:
        await original(**kwargs)
        started.set()

    bot.send_chat_action = observed_action  # type: ignore[method-assign]
    async with typing_status(bot, 42, interval=0.01):  # type: ignore[arg-type]
        await asyncio.wait_for(started.wait(), timeout=0.2)
        await asyncio.sleep(0.03)
    count_at_exit = bot.actions
    await asyncio.sleep(0.03)
    assert count_at_exit >= 2
    assert bot.actions == count_at_exit
