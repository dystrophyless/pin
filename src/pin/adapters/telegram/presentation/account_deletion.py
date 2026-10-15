"""Bilingual, explicit Telegram copy for account deletion confirmation."""

from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.presentation.onboarding import OnboardingButton, OnboardingReply
from pin.contexts.retention.api import DeletionPreview


def render_deletion_preview(preview: DeletionPreview, *, language: str) -> OnboardingReply:
    if language not in {"en", "ru"}:
        raise ValueError("language must be 'ru' or 'en'")
    text = {
        "en": (
            "Account deletion preview. Confirming immediately stops new account work and "
            "starts removal of your plans and other operational data. A minimal record for "
            "prior trial use and current paid access, plus required payment evidence, is "
            "handled separately. Telegram and provider copies are outside this local deletion. "
            "This preview expires in 24 hours."
        ),
        "ru": (
            "Предпросмотр удаления аккаунта. Подтверждение сразу остановит новые операции "
            "и запустит удаление планов и остальных операционных данных. Минимальные сведения "
            "о прошлом пробном периоде и действующем платном доступе, а также обязательные "
            "платёжные документы обрабатываются отдельно. Копии в Telegram и у провайдеров "
            "этим локальным удалением не затрагиваются. Предпросмотр действует 24 часа."
        ),
    }[language]
    buttons = (
        OnboardingButton(
            "Delete account" if language == "en" else "Удалить аккаунт",
            "deletion.confirm",
            str(preview.id),
        ),
        OnboardingButton(
            "Cancel" if language == "en" else "Отмена", "deletion.cancel", str(preview.id)
        ),
    )
    return OnboardingReply(escape_markdown_v2(text), text, buttons=buttons)


def render_deletion_result(*, language: str, confirmed: bool) -> OnboardingReply:
    if language not in {"en", "ru"}:
        raise ValueError("language must be 'en' or 'ru'")
    text = {
        "en": (
            "Deletion is confirmed. Account work is stopped and local cleanup is queued."
            if confirmed
            else "Deletion preview cancelled. Your account and plans are unchanged."
        ),
        "ru": (
            "Удаление подтверждено. Работа аккаунта остановлена, "
            "локальная очистка поставлена в очередь."
            if confirmed
            else "Предпросмотр удаления отменён. Аккаунт и планы не изменились."
        ),
    }[language]
    return OnboardingReply(escape_markdown_v2(text), text)


def render_deletion_unavailable(*, language: str) -> OnboardingReply:
    if language not in {"en", "ru"}:
        raise ValueError("language must be 'ru' or 'en'")
    text = (
        "This deletion preview is no longer current. Send /delete_account to review a new one."
        if language == "en"
        else "Этот предпросмотр больше не актуален. Отправьте /delete_account, чтобы открыть новый."
    )
    return OnboardingReply(escape_markdown_v2(text), text)
