"""Short-transaction, idempotent persistence for actual provider usage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.contexts.billing.api import (
    AccountLifecycle,
    CheckAccess,
    Operation,
    ReservationState,
    ReserveUsage,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.conversation.application.contracts import CallStart, CallUsage, ReservationStatus
from pin.contexts.conversation.infrastructure.models import (
    ConversationTurnResultRecord,
    ProviderCallRecord,
)
from pin.persistence.uow import SqlAlchemyUnitOfWork
from pin.platform.jobs import JobClaim, JobLane, lock_job_for_effects


class ProviderReceiptConflict(RuntimeError):
    """A provider request identity was reused with different financial facts."""


class SqlAlchemyTurnAllowance:
    """Reserve one AI turn through the published billing contract, fenced per job."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        reservation_ttl: timedelta = timedelta(minutes=5),
        voice_seconds: int = 0,
    ) -> None:
        if reservation_ttl <= timedelta(0):
            raise ValueError("reservation_ttl must be positive")
        if type(voice_seconds) is not int or voice_seconds < 0:
            raise ValueError("voice_seconds must be a nonnegative integer")
        self._session_factory = session_factory
        self._reservation_ttl = reservation_ttl
        self._voice_seconds = voice_seconds

    async def reserve(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        at: datetime,
        claim: JobClaim | None = None,
    ) -> ReservationStatus:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("reservation time must be timezone-aware")
        _require_claim_owner(owner_id, claim)
        if self._voice_seconds and (claim is None or claim.lane is not JobLane.SPEECH):
            raise ValueError("prepared voice allowance requires a speech job claim")
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            if claim is not None:
                await lock_job_for_effects(uow.session, claim)
            store = SqlAlchemyAccessUsageStore(uow.session)
            if self._voice_seconds:
                return await self._prepared_voice_status(
                    store, uow.session, owner_id, request_id, at
                )
            access = await store.check_access(CheckAccess(owner_id, Operation.AI_TURN, at))
            expires_at = at + self._reservation_ttl
            if access.allowed and access.period_ends_at is not None:
                expires_at = min(expires_at, access.period_ends_at)
            if expires_at <= at:
                return ReservationStatus.DENIED
            result = await store.reserve_usage(
                ReserveUsage(
                    owner_id=owner_id,
                    request_id=request_id,
                    operation=Operation.AI_TURN,
                    ai_turns=1,
                    voice_seconds=0,
                    at=at,
                    expires_at=expires_at,
                )
            )
            if result.reservation is None:
                return ReservationStatus.DENIED
            return ReservationStatus.RESERVED if result.created else ReservationStatus.REPLAY
        return ReservationStatus.DENIED

    async def _prepared_voice_status(
        self,
        store: SqlAlchemyAccessUsageStore,
        session: AsyncSession,
        owner_id: UUID,
        request_id: str,
        at: datetime,
    ) -> ReservationStatus:
        # Speech committed the combined hold before downloading or recognizing audio.
        access = await store.lock_access(CheckAccess(owner_id, Operation.VOICE_TRANSCRIPTION, at))
        reservation = await store.get_usage_reservation(owner_id, request_id)
        if (
            reservation is None
            or reservation.ai_turns != 1
            or reservation.voice_seconds != self._voice_seconds
        ):
            return ReservationStatus.DENIED
        if reservation.state is ReservationState.SETTLED:
            if await store.lock_account_lifecycle(owner_id) is not AccountLifecycle.PRESENT:
                return ReservationStatus.DENIED
            return ReservationStatus.REPLAY
        if (
            not access.allowed
            or reservation.state is not ReservationState.RESERVED
            or reservation.expires_at <= at
        ):
            return ReservationStatus.DENIED
        started_call = await session.scalar(
            select(ProviderCallRecord.call_id)
            .where(
                ProviderCallRecord.owner_id == owner_id,
                ProviderCallRecord.turn_id == request_id,
            )
            .limit(1)
        )
        # A durable started call is never resubmitted merely because its result is missing.
        return ReservationStatus.REPLAY if started_call is not None else ReservationStatus.RESERVED

    async def settle(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        at: datetime,
        claim: JobClaim | None = None,
    ) -> None:
        _require_claim_owner(owner_id, claim)
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            if claim is not None:
                await lock_job_for_effects(uow.session, claim)
            await SqlAlchemyAccessUsageStore(uow.session).settle_usage(
                owner_id=owner_id,
                request_id=request_id,
                ai_turns=1,
                voice_seconds=self._voice_seconds,
                at=at,
            )

    async def refund(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        at: datetime,
        claim: JobClaim | None = None,
    ) -> None:
        _require_claim_owner(owner_id, claim)
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            if claim is not None:
                await lock_job_for_effects(uow.session, claim)
            await SqlAlchemyAccessUsageStore(uow.session).refund_usage(
                owner_id=owner_id,
                request_id=request_id,
                at=at,
            )


class SqlAlchemyProviderUsageRecorder:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start_call(self, call: CallStart, *, claim: JobClaim | None = None) -> None:
        _require_claim_owner(call.owner_id, claim)
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            await self._fence(uow.session, claim)
            existing = await uow.session.scalar(
                select(ProviderCallRecord).where(
                    ProviderCallRecord.owner_id == call.owner_id,
                    ProviderCallRecord.turn_id == call.turn_id,
                    ProviderCallRecord.call_id == call.call_id,
                )
            )
            if existing is not None:
                if (
                    existing.call_number != call.call_number
                    or existing.model != call.model
                    or existing.created_at != call.created_at
                ):
                    raise ProviderReceiptConflict("local provider call key was reused")
                return
            uow.session.add(
                ProviderCallRecord(
                    owner_id=call.owner_id,
                    turn_id=call.turn_id,
                    call_id=call.call_id,
                    call_number=call.call_number,
                    model=call.model,
                    state="started",
                    result_category="pending",
                    created_at=call.created_at,
                )
            )

    async def record_usage(
        self,
        call: CallStart,
        usage: CallUsage,
        *,
        result_category: str,
        claim: JobClaim | None = None,
    ) -> None:
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            # A late provider response is still a financial fact after its job lease expires.
            row = await self._call_for_update(uow.session, call)
            by_request = (
                await uow.session.scalar(
                    select(ProviderCallRecord).where(
                        ProviderCallRecord.provider_request_id == usage.provider_request_id
                    )
                )
                if usage.provider_request_id is not None
                else None
            )
            if by_request is not None and by_request.call_id != row.call_id:
                if not self._same_receipt(by_request, call, usage):
                    raise ProviderReceiptConflict("provider request ID has conflicting usage facts")
                self._merge_usage(by_request, usage)
                if all(
                    value is not None
                    for value in (
                        by_request.prompt_tokens,
                        by_request.completion_tokens,
                        by_request.total_tokens,
                    )
                ):
                    by_request.state = "reported"
                row.state = "deduplicated"
                row.duplicate_of_call_id = by_request.call_id
                row.prompt_tokens = None
                row.completion_tokens = None
                row.total_tokens = None
                row.cost_usd = None
                row.latency_ms = None
                row.result_category = "duplicate_receipt"
                row.finished_at = datetime.now(UTC)
                return
            if row.state == "deduplicated":
                raise ProviderReceiptConflict("deduplicated provider call cannot be overwritten")
            self._merge_usage(row, usage)
            row.state = (
                "reported"
                if all(
                    value is not None
                    for value in (row.prompt_tokens, row.completion_tokens, row.total_tokens)
                )
                else "unknown"
            )
            row.result_category = result_category
            row.finished_at = datetime.now(UTC)

    async def mark_unknown(
        self,
        call: CallStart,
        *,
        result_category: str,
        provider_request_id: str | None = None,
        claim: JobClaim | None = None,
    ) -> None:
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            # Preserve uncertainty for an already-started call even after lease expiry.
            row = await self._call_for_update(uow.session, call)
            if row.state == "reported":
                return
            if row.state == "deduplicated":
                return
            if row.provider_request_id not in (None, provider_request_id):
                raise ProviderReceiptConflict("provider request ID changed for an unknown call")
            row.provider_request_id = provider_request_id or row.provider_request_id
            row.state = "unknown"
            row.result_category = result_category
            row.finished_at = datetime.now(UTC)

    async def store_result(
        self,
        *,
        owner_id: UUID,
        turn_id: str,
        result: dict[str, object],
        provider_request_ids: tuple[str, ...],
        claim: JobClaim | None = None,
    ) -> None:
        _require_claim_owner(owner_id, claim)
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            await self._fence(uow.session, claim)
            existing = await uow.session.scalar(
                select(ConversationTurnResultRecord)
                .where(
                    ConversationTurnResultRecord.owner_id == owner_id,
                    ConversationTurnResultRecord.turn_id == turn_id,
                )
                .with_for_update()
            )
            if existing is not None:
                if (
                    existing.result != result
                    or tuple(existing.provider_request_ids) != provider_request_ids
                ):
                    raise ProviderReceiptConflict(
                        "turn result key was reused with different output"
                    )
                return
            uow.session.add(
                ConversationTurnResultRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    turn_id=turn_id,
                    result=result,
                    provider_request_ids=list(provider_request_ids),
                    created_at=datetime.now(UTC),
                )
            )

    async def get_result(
        self, *, owner_id: UUID, turn_id: str
    ) -> tuple[dict[str, object], tuple[str, ...]] | None:
        async with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            row = await uow.session.scalar(
                select(ConversationTurnResultRecord).where(
                    ConversationTurnResultRecord.owner_id == owner_id,
                    ConversationTurnResultRecord.turn_id == turn_id,
                )
            )
            if row is None:
                return None
            return row.result, tuple(row.provider_request_ids)
        return None

    @staticmethod
    async def _fence(session: AsyncSession, claim: JobClaim | None) -> None:
        if claim is not None:
            await lock_job_for_effects(session, claim)

    @staticmethod
    async def _call_for_update(session: AsyncSession, call: CallStart) -> ProviderCallRecord:
        row = await session.scalar(
            select(ProviderCallRecord)
            .where(
                ProviderCallRecord.owner_id == call.owner_id,
                ProviderCallRecord.turn_id == call.turn_id,
                ProviderCallRecord.call_id == call.call_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ProviderReceiptConflict("provider call was not reserved before network I/O")
        return row

    @staticmethod
    def _same_receipt(row: ProviderCallRecord, call: CallStart, usage: CallUsage) -> bool:
        return (
            usage.provider_request_id is not None
            and row.owner_id == call.owner_id
            and row.turn_id == call.turn_id
            and row.state in {"reported", "unknown"}
            and row.provider_request_id == usage.provider_request_id
            and row.model == usage.model
            and all(
                saved is None or received is None or saved == received
                for saved, received in (
                    (row.prompt_tokens, usage.prompt_tokens),
                    (row.completion_tokens, usage.completion_tokens),
                    (row.total_tokens, usage.total_tokens),
                    (row.cost_usd, usage.cost_usd),
                )
            )
        )

    @staticmethod
    def _merge_usage(row: ProviderCallRecord, usage: CallUsage) -> None:
        if row.provider_request_id not in (None, usage.provider_request_id):
            raise ProviderReceiptConflict("provider request ID changed for a call")
        if row.model != usage.model:
            raise ProviderReceiptConflict("provider model changed for a call")
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"):
            saved = getattr(row, name)
            received = getattr(usage, name)
            if saved is not None and received is not None and saved != received:
                raise ProviderReceiptConflict("provider usage changed after it was recorded")
            if saved is None and received is not None:
                setattr(row, name, received)
        row.provider_request_id = usage.provider_request_id or row.provider_request_id
        row.latency_ms = usage.latency_ms


def _require_claim_owner(owner_id: UUID, claim: JobClaim | None) -> None:
    if claim is not None and claim.owner_id != owner_id:
        raise ValueError("job claim owner does not match conversation owner")
