"""Speech-owned adapter to Billing's public voice reservation contract."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.contexts.billing.api import (
    CheckAccess,
    InvalidTransition,
    Operation,
    QuotaExceeded,
    ReservationState,
    ReserveUsage,
    ResizeVoiceUsage,
    SqlAlchemyAccessUsageStore,
    UsageReservation,
)
from pin.contexts.identity.api import (
    ProcessingAuthorization,
    SqlAlchemyIdentityStore,
    authorize_processing_consent,
)
from pin.persistence.uow import SqlAlchemyUnitOfWork
from pin.platform.jobs import JobClaim, lock_job_for_effects


class SqlAlchemySpeechAllowance:
    """Reserve before download, resize to decoded duration, then refund on audio failure."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        reservation_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if reservation_ttl <= timedelta(0):
            raise ValueError("reservation_ttl must be positive")
        self._sessions = sessions
        self._reservation_ttl = reservation_ttl

    async def reserve(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        voice_seconds: int,
        at: datetime,
        claim: JobClaim,
    ) -> UsageReservation | None:
        _validate(owner_id, request_id, voice_seconds, at, claim)
        async with SqlAlchemyUnitOfWork(self._sessions) as uow:
            reservation = await self.reserve_in(
                uow.session,
                owner_id=owner_id,
                request_id=request_id,
                voice_seconds=voice_seconds,
                at=at,
                claim=claim,
            )
        return reservation

    async def reserve_in(
        self,
        session: AsyncSession,
        *,
        owner_id: UUID,
        request_id: str,
        voice_seconds: int,
        at: datetime,
        claim: JobClaim,
    ) -> UsageReservation | None:
        _validate(owner_id, request_id, voice_seconds, at, claim)
        identity = SqlAlchemyIdentityStore(session)
        if claim.processing_grant_id is None or claim.processing_policy_version is None:
            await identity.lock_processing_consent(owner_id)
            return None
        if not await authorize_processing_consent(
            identity,
            owner_id=owner_id,
            authorization=ProcessingAuthorization(
                owner_id, claim.processing_grant_id, claim.processing_policy_version
            ),
        ):
            return None
        await lock_job_for_effects(session, claim)
        billing = SqlAlchemyAccessUsageStore(session)
        previous = await billing.get_usage_reservation(owner_id, request_id)
        if previous is not None:
            if previous.ai_turns != 1 or previous.voice_seconds != voice_seconds:
                raise InvalidTransition("voice request key was replayed with different quota facts")
            if previous.state is not ReservationState.RESERVED or previous.expires_at <= at:
                raise InvalidTransition("voice quota reservation is no longer open")
            return previous
        access = await billing.check_access(
            CheckAccess(owner_id, Operation.VOICE_TRANSCRIPTION, at)
        )
        if not access.allowed or access.period_ends_at is None:
            return None
        expires_at = min(at + self._reservation_ttl, access.period_ends_at)
        if expires_at <= at:
            return None
        result = await billing.reserve_usage(
            ReserveUsage(
                owner_id=owner_id,
                request_id=request_id,
                operation=Operation.VOICE_TRANSCRIPTION,
                ai_turns=1,
                voice_seconds=voice_seconds,
                at=at,
                expires_at=expires_at,
            )
        )
        return result.reservation

    async def resize(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        voice_seconds: int,
        at: datetime,
        claim: JobClaim,
    ) -> UsageReservation:
        _validate(owner_id, request_id, voice_seconds, at, claim)
        async with SqlAlchemyUnitOfWork(self._sessions) as uow:
            reservation = await self.resize_in(
                uow.session,
                owner_id=owner_id,
                request_id=request_id,
                voice_seconds=voice_seconds,
                at=at,
                claim=claim,
            )
        return reservation

    async def resize_in(
        self,
        session: AsyncSession,
        *,
        owner_id: UUID,
        request_id: str,
        voice_seconds: int,
        at: datetime,
        claim: JobClaim,
    ) -> UsageReservation:
        _validate(owner_id, request_id, voice_seconds, at, claim)
        await SqlAlchemyIdentityStore(session).lock_processing_consent(owner_id)
        await lock_job_for_effects(session, claim)
        return await SqlAlchemyAccessUsageStore(session).resize_voice_usage(
            ResizeVoiceUsage(owner_id, request_id, voice_seconds, at)
        )

    async def refund(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        at: datetime,
        claim: JobClaim,
    ) -> UsageReservation:
        if claim.owner_id != owner_id or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("refund requires the owner's live claim and aware time")
        async with SqlAlchemyUnitOfWork(self._sessions) as uow:
            reservation = await self.refund_in(
                uow.session, owner_id=owner_id, request_id=request_id, at=at, claim=claim
            )
        return reservation

    async def refund_in(
        self,
        session: AsyncSession,
        *,
        owner_id: UUID,
        request_id: str,
        at: datetime,
        claim: JobClaim,
    ) -> UsageReservation:
        if claim.owner_id != owner_id or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("refund requires the owner's live claim and aware time")
        await SqlAlchemyIdentityStore(session).lock_processing_consent(owner_id)
        await lock_job_for_effects(session, claim)
        return await SqlAlchemyAccessUsageStore(session).refund_usage(
            owner_id=owner_id, request_id=request_id, at=at
        )


def _validate(
    owner_id: UUID,
    request_id: str,
    voice_seconds: int,
    at: datetime,
    claim: JobClaim,
) -> None:
    if claim.owner_id != owner_id or claim.lane.value != "speech":
        raise ValueError("voice reservation requires the owner's speech job claim")
    if not request_id or len(request_id) > 128:
        raise ValueError("request_id must contain 1 to 128 characters")
    if type(voice_seconds) is not int or voice_seconds <= 0:
        raise ValueError("voice_seconds must be positive")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("reservation time must be timezone-aware")


__all__ = ["QuotaExceeded", "SqlAlchemySpeechAllowance"]
