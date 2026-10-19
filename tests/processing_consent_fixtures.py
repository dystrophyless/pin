from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.api import (
    PROCESSING_CONSENT_POLICY_VERSION,
    ProcessingAuthorization,
    SqlAlchemyIdentityStore,
    accept_processing_consent,
)
from pin.platform.models import InboxUpdate


async def seed_accepted_processing_consent(
    session: AsyncSession,
    *,
    owner_id: UUID,
    bot_id: int,
    telegram_user_id: int,
    expected_revision: int = 0,
) -> ProcessingAuthorization:
    """Seed the same owner-bound acceptance facts the onboarding callback persists."""
    update_id = uuid4()
    session.add(
        InboxUpdate(
            id=update_id,
            bot_id=bot_id,
            update_id=uuid4().int % 9_000_000_000_000_000,
            owner_id=owner_id,
            update_type="callback_query",
            payload={
                "callback_query": {
                    "from": {"id": telegram_user_id, "is_bot": False},
                    "message": {"chat": {"id": telegram_user_id, "type": "private"}},
                }
            },
        )
    )
    await session.flush()
    consent = await accept_processing_consent(
        SqlAlchemyIdentityStore(session),
        owner_id=owner_id,
        expected_revision=expected_revision,
        policy_version=PROCESSING_CONSENT_POLICY_VERSION,
        language="en",
        accepted_at=datetime.now(UTC),
        telegram_user_id=telegram_user_id,
        inbox_update_id=update_id,
    )
    assert consent.grant_id is not None and consent.policy_version is not None
    return ProcessingAuthorization(owner_id, consent.grant_id, consent.policy_version)
