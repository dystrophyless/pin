from uuid import uuid4

import pytest

from pin.adapters.telegram.application_worker import _privacy_status_text
from pin.adapters.telegram.presentation.onboarding import render_onboarding
from pin.adapters.telegram.presentation.templates import (
    plain_help,
    plain_privacy,
    render_privacy,
)
from pin.contexts.identity.application.contracts import ProcessingAuthorization
from pin.contexts.identity.application.onboarding import OnboardingResult
from pin.contexts.identity.application.processing_consent import authorize_processing_consent
from pin.persistence import run_local_async


@pytest.mark.parametrize(
    ("language", "required_text"),
    [
        (
            "en",
            (
                "OpenRouter",
                "voice transcripts",
                "does not itself authorize sharing",
                "24 hours",
                "30 days",
                "7 days",
                "model training",
                "/delete_account",
                "statutory period has not been verified",
            ),
        ),
        (
            "ru",
            (
                "OpenRouter",
                "голосовые сообщения",
                "расшифровки голоса",
                "само по себе не означает согласия",
                "24 часов",
                "30 дней",
                "7 дней",
                "обучения моделей",
                "/delete_account",
                "срок пока не подтверждён",
            ),
        ),
    ],
)
def test_privacy_copy_is_localized_escaped_and_fits_telegram(
    language: str, required_text: tuple[str, ...]
) -> None:
    plain = plain_privacy(language)
    rendered = render_privacy(language)

    assert len(plain) < 4096
    assert len(rendered) < 4096
    assert all(part in plain for part in required_text)
    assert "OpenRouter" in rendered
    assert r"/delete\_account" in rendered


def test_help_lists_privacy_in_both_languages() -> None:
    assert "/privacy" in plain_help("en")
    assert "/privacy" in plain_help("ru")


def test_privacy_copy_rejects_unsupported_languages() -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        plain_privacy("kk")


@pytest.mark.parametrize(
    ("language", "button", "notice"),
    [
        (
            "en",
            "Agree and finish setup",
            "By choosing Agree and finish setup, you consent to this processing.",
        ),
        (
            "ru",
            "Согласиться и завершить настройку",
            "Нажимая «Согласиться и завершить настройку», вы соглашаетесь на эту обработку.",
        ),
    ],
)
def test_initial_onboarding_requires_the_explicit_processing_consent_action(
    language: str, button: str, notice: str
) -> None:
    reply = render_onboarding(
        OnboardingResult(
            "advanced",
            step="confirm",
            mode="initial",
            draft={
                "language": language,
                "home_zone": "Asia/Almaty",
                "reminder_calendar_days": 1,
                "reminder_elapsed_hours": 1,
                "quiet_start": "22:00",
                "quiet_end": "08:00",
                "review_time": "20:00",
            },
        )
    )
    assert reply is not None
    assert notice in reply.plain_text
    separator_and_copy = (
        "\nPin uses external AI services"
        if language == "en"
        else "\nPin использует внешние ИИ-сервисы"
    )
    assert separator_and_copy in reply.plain_text
    assert r"\n" not in reply.plain_text
    assert r"\n" not in reply.markdown
    assert [item.text for item in reply.buttons] == [
        button,
        "Details" if language == "en" else "Подробнее",
        "Cancel" if language == "en" else "Отмена",
    ]
    assert reply.buttons[0].action == "consent.accept"


def test_privacy_consent_status_uses_visible_paragraph_breaks() -> None:
    notice = "Privacy details"
    status = "Consent has not been given."
    text = _privacy_status_text(notice, status)
    assert text == "Privacy details\n\nConsent has not been given."
    assert r"\n" not in text


def test_authorization_does_not_default_allow_when_identity_storage_fails() -> None:
    class UnavailableIdentityStore:
        async def authorize_processing_consent(self, owner_id, authorization):
            raise RuntimeError("identity storage unavailable")

    async def scenario() -> None:
        owner_id = uuid4()
        authorization = ProcessingAuthorization(
            owner_id=owner_id,
            grant_id=uuid4(),
            policy_version="processing-consent-v1",
        )
        with pytest.raises(RuntimeError, match="identity storage unavailable"):
            await authorize_processing_consent(
                UnavailableIdentityStore(),
                owner_id=owner_id,
                authorization=authorization,
            )

    run_local_async(scenario)
