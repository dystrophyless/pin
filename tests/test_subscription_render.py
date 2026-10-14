from datetime import UTC, datetime

import pytest

from pin.adapters.telegram.presentation.subscription import (
    render_access_expiry_notice,
    render_subscription_status,
)
from pin.contexts.billing.api import AccountLifecycle, PeriodKind, SubscriptionStatus


@pytest.mark.parametrize("language", ["ru", "en"])
def test_exhausted_active_access_still_reports_reminders_and_exact_voice(language: str) -> None:
    from uuid import uuid4

    status = SubscriptionStatus(
        uuid4(),
        AccountLifecycle.PRESENT,
        "paid-current",
        PeriodKind.PAID,
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 31, 9, tzinfo=UTC),
        True,
        True,
        1000,
        0,
        3600,
        61,
    )
    reply = render_subscription_status(status, language=language, zone_name="Asia/Almaty")
    assert "31.10.2026 14:00 (Asia/Almaty)" in reply.plain_text
    assert (
        "Осталось ИИ-запросов: 0" if language == "ru" else "AI turns remaining: 0"
    ) in reply.plain_text
    assert ("1 мин 1 с" if language == "ru" else "1 min 1 sec") in reply.plain_text
    assert (
        "Напоминания работают" if language == "ru" else "Reminders continue"
    ) in reply.plain_text
    assert not reply.buttons


@pytest.mark.parametrize("language", ["ru", "en"])
def test_expiry_notice_states_absolute_time_instead_of_stale_relative_lead(language: str) -> None:
    reply = render_access_expiry_notice(
        period_kind=PeriodKind.TRIAL,
        ends_at=datetime(2026, 10, 14, 10, 30, tzinfo=UTC),
        language=language,
        zone_name="Asia/Almaty",
    )
    assert "14.10.2026 15:30 (Asia/Almaty)" in reply.plain_text
    assert "/subscription" in reply.plain_text
    assert "24" not in reply.plain_text and "in an hour" not in reply.plain_text
    assert "14\\.10\\.2026" in reply.markdown
    assert not reply.buttons
