"""Caller-UoW Telegram handoff for home-zone previews and confirmation."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.api import AccessUsageStore
from pin.contexts.identity.api import IdentityStore
from pin.contexts.planning.application.home_zone_changes import (
    HomeZoneChangeClock,
    HomeZoneChangePreview,
    HomeZoneChangeProof,
    HomeZoneChangeRequest,
    HomeZoneChangeVerifier,
    confirm_home_zone_change,
    preview_home_zone_change,
)
from pin.contexts.planning.infrastructure.home_zone_change_store import (
    SqlAlchemyHomeZoneChangeStore,
)


async def preview_home_zone_change_for_telegram(
    request: HomeZoneChangeRequest,
    *,
    session: AsyncSession,
    identity: IdentityStore,
    billing: AccessUsageStore,
    clock: HomeZoneChangeClock,
) -> HomeZoneChangePreview:
    """Persist a preview in a savepoint; the caller owns the outer transaction."""
    async with session.begin_nested():
        return await preview_home_zone_change(
            request,
            store=SqlAlchemyHomeZoneChangeStore(session),
            identity=identity,
            billing=billing,
            clock=clock,
        )


async def confirm_home_zone_change_for_telegram(
    proof: HomeZoneChangeProof,
    *,
    authenticated_owner_id: UUID,
    session: AsyncSession,
    identity: IdentityStore,
    billing: AccessUsageStore,
    verifier: HomeZoneChangeVerifier,
    clock: HomeZoneChangeClock,
) -> dict[str, object]:
    """Apply Identity and Planning changes atomically without committing the UoW."""
    async with session.begin_nested():
        return await confirm_home_zone_change(
            proof,
            authenticated_owner_id=authenticated_owner_id,
            store=SqlAlchemyHomeZoneChangeStore(session),
            identity=identity,
            billing=billing,
            verifier=verifier,
            clock=clock,
        )
