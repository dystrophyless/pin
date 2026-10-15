"""Owner-bound Telegram handlers for the retention deletion preview contract."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.presentation.account_deletion import (
    render_deletion_preview,
    render_deletion_result,
    render_deletion_unavailable,
)
from pin.adapters.telegram.presentation.onboarding import OnboardingReply
from pin.contexts.retention.domain.deletion import DeletionRejected
from pin.contexts.retention.infrastructure.store import (
    cancel_deletion_preview,
    confirm_deletion_preview,
    create_deletion_preview,
    get_current_deletion_preview,
)


async def begin_account_deletion(
    session: AsyncSession, *, owner_id: UUID, language: str, now: datetime
) -> OnboardingReply:
    try:
        preview = await create_deletion_preview(session, owner_id=owner_id, now=now)
    except DeletionRejected:
        return render_deletion_unavailable(language=language)
    return render_deletion_preview(preview, language=language)


async def current_deletion_preview_version(
    session: AsyncSession, *, owner_id: UUID, now: datetime
) -> int:
    preview = await get_current_deletion_preview(session, owner_id=owner_id, now=now)
    return preview.version if preview is not None else 0


async def apply_account_deletion_callback(
    session: AsyncSession,
    *,
    owner_id: UUID,
    action: str,
    preview_id: str,
    expected_version: int,
    language: str,
    now: datetime,
) -> OnboardingReply:
    try:
        target_id = UUID(preview_id)
        preview = await get_current_deletion_preview(session, owner_id=owner_id, now=now)
        if preview is None or preview.id != target_id or preview.version != expected_version:
            return render_deletion_unavailable(language=language)
        if action == "deletion.cancel":
            await cancel_deletion_preview(
                session,
                owner_id=owner_id,
                preview_id=target_id,
                expected_version=expected_version,
                now=now,
            )
            return render_deletion_result(language=language, confirmed=False)
        if action == "deletion.confirm":
            await confirm_deletion_preview(
                session,
                owner_id=owner_id,
                preview_id=target_id,
                expected_version=expected_version,
                now=now,
            )
            return render_deletion_result(language=language, confirmed=True)
    except (ValueError, DeletionRejected):
        return render_deletion_unavailable(language=language)
    return render_deletion_unavailable(language=language)
