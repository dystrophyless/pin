"""SQLAlchemy implementation of the atomic owner-scoped usage boundary."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.application.contracts import (
    AccessUsageStore,
    CheckAccess,
    ReserveUsage,
    ReserveUsageResult,
    StartTrial,
    TrialActivationResult,
    TrialActivationStatus,
)
from pin.contexts.billing.domain.access import (
    AccessAccount,
    AccessDecision,
    AccessPeriod,
    Allowance,
    InvalidTransition,
    Operation,
    PeriodKind,
    PlanPolicy,
    ReservationState,
    UsageLedger,
    UsageReservation,
)
from pin.contexts.billing.domain.access import (
    check_access as decide_access,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    TrialActivationRecord,
    UsageReservationRecord,
)


class SqlAlchemyAccessUsageStore(AccessUsageStore):
    """Use the caller's transaction; account and period row locks serialize usage."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def activate_trial(self, command: StartTrial) -> TrialActivationResult:
        if type(command.telegram_user_id) is not int or command.telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be a positive integer")
        if command.at.tzinfo is None or command.at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")

        await self._session.execute(
            pg_insert(AccessAccountRecord)
            .values(owner_id=command.owner_id)
            .on_conflict_do_nothing(index_elements=[AccessAccountRecord.owner_id])
        )
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .with_for_update()
        )
        if account is None:
            raise InvalidTransition("access account could not be established")

        prior = await self._session.get(TrialActivationRecord, command.owner_id)
        if prior is not None:
            if prior.telegram_user_id != command.telegram_user_id:
                raise InvalidTransition("owner does not match the prior trial identity")
        other_owner = await self._session.scalar(
            select(TrialActivationRecord.owner_id).where(
                TrialActivationRecord.telegram_user_id == command.telegram_user_id
            )
        )
        if other_owner is not None and other_owner != command.owner_id:
            raise InvalidTransition("Telegram identity already belongs to another trial owner")

        periods = await self._active_periods(command.owner_id, command.at, lock=True)
        active = next(
            (row for row in periods if row.policy_kind == PeriodKind.PAID.value),
            periods[0] if periods else None,
        )
        if active is not None:
            if prior is not None and active.policy_kind == PeriodKind.TRIAL.value:
                if account.deleted_at is not None:
                    prior.revoked_at = command.at
                    account.deleted_at = None
                return TrialActivationResult(
                    TrialActivationStatus.PREVIOUSLY_ACTIVATED,
                    prior.period_id,
                    active.starts_at,
                    active.ends_at,
                )
            if account.deleted_at is not None:
                if active.policy_kind != PeriodKind.PAID.value:
                    return TrialActivationResult(
                        TrialActivationStatus.PREVIOUSLY_ACTIVATED,
                        prior.period_id if prior is not None else active.period_id,
                        None,
                        None,
                    )
                account.deleted_at = None
            return TrialActivationResult(
                TrialActivationStatus.ALREADY_ACTIVE,
                active.period_id,
                active.starts_at,
                active.ends_at,
            )
        if prior is not None:
            if account.deleted_at is not None:
                # Confirmed return restores the account, not expired entitlement.
                account.deleted_at = None
            return TrialActivationResult(
                TrialActivationStatus.PREVIOUSLY_ACTIVATED,
                prior.period_id,
                None,
                None,
            )
        if account.deleted_at is not None:
            return TrialActivationResult(
                TrialActivationStatus.PREVIOUSLY_ACTIVATED,
                prior.period_id if prior is not None else None,
                None,
                None,
            )

        policy = PlanPolicy.trial()
        period_id = f"trial-{command.owner_id}"
        ends_at = command.at + policy.duration
        self._session.add(
            AccessPeriodRecord(
                owner_id=command.owner_id,
                period_id=period_id,
                policy_kind=PeriodKind.TRIAL.value,
                policy_version=policy.version,
                starts_at=command.at,
                ends_at=ends_at,
                ai_turn_limit=policy.allowance.ai_turns,
                voice_second_limit=policy.allowance.voice_seconds,
            )
        )
        self._session.add(
            TrialActivationRecord(
                owner_id=command.owner_id,
                telegram_user_id=command.telegram_user_id,
                period_id=period_id,
                activated_at=command.at,
            )
        )
        return TrialActivationResult(
            TrialActivationStatus.ACTIVATED, period_id, command.at, ends_at
        )

    async def check_access(self, query: CheckAccess) -> AccessDecision:
        periods = await self._active_periods(query.owner_id, query.at, lock=False)
        account_record = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == query.owner_id)
            .execution_options(populate_existing=True)
        )
        account = AccessAccount(
            tuple(_period_from_record(row) for row in periods),
            deleted=account_record is None or account_record.deleted_at is not None,
        )
        return decide_access(account, query.operation, query.at)

    async def reserve_usage(self, command: ReserveUsage) -> ReserveUsageResult:
        if command.operation not in {Operation.AI_TURN, Operation.VOICE_TRANSCRIPTION}:
            raise ValueError("usage can only be reserved for AI turns or voice transcription")
        _validate_operation_units(command.operation, command.ai_turns, command.voice_seconds)

        account_row = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .with_for_update()
        )
        if account_row is None:
            denied = decide_access(AccessAccount(deleted=True), command.operation, command.at)
            return ReserveUsageResult(denied, None)
        if account_row.deleted_at is not None:
            denied = decide_access(AccessAccount(deleted=True), command.operation, command.at)
            return ReserveUsageResult(denied, None)

        active = await self._active_periods(command.owner_id, command.at, lock=True)
        account = AccessAccount(tuple(_period_from_record(row) for row in active))
        access = decide_access(account, command.operation, command.at)

        previous = await self._session.scalar(
            select(UsageReservationRecord).where(
                UsageReservationRecord.owner_id == command.owner_id,
                UsageReservationRecord.request_id == command.request_id,
            )
        )
        if previous is not None:
            previous_period = await self._period_for_update(command.owner_id, previous.period_id)
            if previous_period is None:
                raise InvalidTransition("reservation period no longer exists")
            ledger, rows = await self._ledger(previous_period, lock=True)
            expired = ledger.expire(at=command.at)
            self._persist_expired(rows, expired)
            reservation = ledger.reserve(
                request_id=command.request_id,
                period_id=previous.period_id,
                ai_turns=command.ai_turns,
                voice_seconds=command.voice_seconds,
                at=command.at,
                expires_at=command.expires_at,
            )
            return ReserveUsageResult(
                access,
                reservation,
            )

        if not access.allowed or access.period_id is None:
            return ReserveUsageResult(access, None)

        period_row = next(row for row in active if row.period_id == access.period_id)
        ledger, rows = await self._ledger(period_row, lock=True)
        expired = ledger.expire(at=command.at)
        self._persist_expired(rows, expired)
        reservation = ledger.reserve(
            request_id=command.request_id,
            period_id=access.period_id,
            ai_turns=command.ai_turns,
            voice_seconds=command.voice_seconds,
            at=command.at,
            expires_at=command.expires_at,
        )
        self._session.add(
            UsageReservationRecord(
                owner_id=command.owner_id,
                period_id=access.period_id,
                request_id=command.request_id,
                reserved_ai_turns=command.ai_turns,
                reserved_voice_seconds=command.voice_seconds,
                settled_ai_turns=0,
                settled_voice_seconds=0,
                state=reservation.state.value,
                created_at=command.at,
                expires_at=command.expires_at,
            )
        )
        return ReserveUsageResult(access, reservation, created=True)

    async def settle_usage(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        ai_turns: int,
        voice_seconds: int,
        at: datetime,
    ) -> UsageReservation:
        record = await self._reservation_for_update(owner_id, request_id)
        period_row = await self._period_for_update(owner_id, record.period_id)
        if period_row is None:
            raise InvalidTransition("reservation period no longer exists")
        ledger, _ = await self._ledger(period_row, lock=True)
        settled = ledger.settle(
            request_id=request_id, ai_turns=ai_turns, voice_seconds=voice_seconds, at=at
        )
        _store_terminal(record, settled, at)
        return settled

    async def refund_usage(
        self, *, owner_id: UUID, request_id: str, at: datetime
    ) -> UsageReservation:
        record = await self._reservation_for_update(owner_id, request_id)
        period_row = await self._period_for_update(owner_id, record.period_id)
        if period_row is None:
            raise InvalidTransition("reservation period no longer exists")
        ledger, _ = await self._ledger(period_row, lock=True)
        refunded = ledger.refund(request_id=request_id, at=at)
        _store_terminal(record, refunded, at)
        return refunded

    async def expire_reservations(self, *, at: datetime, limit: int) -> int:
        if limit <= 0:
            raise ValueError("limit must be positive")
        due = (
            select(UsageReservationRecord)
            .where(
                UsageReservationRecord.state == ReservationState.RESERVED.value,
                UsageReservationRecord.expires_at <= at,
            )
            .order_by(UsageReservationRecord.expires_at, UsageReservationRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        records = list((await self._session.scalars(due)).all())
        for record in records:
            record.state = ReservationState.EXPIRED.value
            record.finalized_at = at
        return len(records)

    async def _active_periods(
        self, owner_id: UUID, at: datetime, *, lock: bool
    ) -> list[AccessPeriodRecord]:
        query = (
            select(AccessPeriodRecord)
            .where(
                AccessPeriodRecord.owner_id == owner_id,
                AccessPeriodRecord.starts_at <= at,
                AccessPeriodRecord.ends_at > at,
            )
            .order_by(
                case((AccessPeriodRecord.policy_kind == PeriodKind.PAID.value, 1), else_=0).desc(),
                AccessPeriodRecord.starts_at.desc(),
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        periods = list((await self._session.scalars(query)).all())
        revoked_trial_id = await self._session.scalar(
            select(TrialActivationRecord.period_id).where(
                TrialActivationRecord.owner_id == owner_id,
                TrialActivationRecord.revoked_at.is_not(None),
            )
        )
        if revoked_trial_id is not None:
            periods = [
                period
                for period in periods
                if not (
                    period.period_id == revoked_trial_id
                    and period.policy_kind == PeriodKind.TRIAL.value
                )
            ]
        return periods

    async def _period_for_update(self, owner_id: UUID, period_id: str) -> AccessPeriodRecord | None:
        record: AccessPeriodRecord | None = await self._session.scalar(
            select(AccessPeriodRecord)
            .where(
                AccessPeriodRecord.owner_id == owner_id,
                AccessPeriodRecord.period_id == period_id,
            )
            .with_for_update()
        )
        return record

    async def _reservation_for_update(
        self, owner_id: UUID, request_id: str
    ) -> UsageReservationRecord:
        period_id = await self._session.scalar(
            select(UsageReservationRecord.period_id).where(
                UsageReservationRecord.owner_id == owner_id,
                UsageReservationRecord.request_id == request_id,
            )
        )
        if period_id is None:
            raise InvalidTransition("reservation does not exist for this owner")
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == owner_id)
            .with_for_update()
        )
        if account is None:
            raise InvalidTransition("access account no longer exists")
        period = await self._period_for_update(owner_id, period_id)
        if period is None:
            raise InvalidTransition("reservation period no longer exists")
        record = await self._session.scalar(
            select(UsageReservationRecord)
            .where(
                UsageReservationRecord.owner_id == owner_id,
                UsageReservationRecord.request_id == request_id,
            )
            .with_for_update()
        )
        if record is None:
            raise InvalidTransition("reservation no longer exists")
        return record

    async def _ledger(
        self, period: AccessPeriodRecord, *, lock: bool
    ) -> tuple[UsageLedger, dict[str, UsageReservationRecord]]:
        policy = PlanPolicy(
            kind=PeriodKind(period.policy_kind),
            duration=period.ends_at - period.starts_at,
            allowance=Allowance(period.ai_turn_limit, period.voice_second_limit),
            version=period.policy_version,
        )
        domain_reservations = await self._session.scalars(
            select(UsageReservationRecord)
            .where(
                UsageReservationRecord.owner_id == period.owner_id,
                UsageReservationRecord.period_id == period.period_id,
            )
            .order_by(UsageReservationRecord.request_id)
            .with_for_update()
            if lock
            else select(UsageReservationRecord)
            .where(
                UsageReservationRecord.owner_id == period.owner_id,
                UsageReservationRecord.period_id == period.period_id,
            )
            .order_by(UsageReservationRecord.request_id)
        )
        rows = {row.request_id: row for row in domain_reservations}
        ledger = UsageLedger.restore(
            period_id=period.period_id,
            allowance=policy.allowance,
            period_ends_at=period.ends_at,
            reservations=tuple(_reservation_from_record(row) for row in rows.values()),
        )
        return ledger, rows

    @staticmethod
    def _persist_expired(
        rows: dict[str, UsageReservationRecord], expired: tuple[UsageReservation, ...]
    ) -> None:
        for reservation in expired:
            record = rows.get(reservation.request_id)
            if record is not None:
                record.state = ReservationState.EXPIRED.value
                record.finalized_at = reservation.expires_at


def _period_from_record(record: AccessPeriodRecord) -> AccessPeriod:
    return AccessPeriod(
        period_id=record.period_id,
        policy=PlanPolicy(
            kind=PeriodKind(record.policy_kind),
            duration=record.ends_at - record.starts_at,
            allowance=Allowance(record.ai_turn_limit, record.voice_second_limit),
            version=record.policy_version,
        ),
        starts_at=record.starts_at,
        ends_at=record.ends_at,
    )


def _reservation_from_record(record: UsageReservationRecord) -> UsageReservation:
    return UsageReservation(
        request_id=record.request_id,
        period_id=record.period_id,
        ai_turns=record.reserved_ai_turns,
        voice_seconds=record.reserved_voice_seconds,
        created_at=record.created_at,
        expires_at=record.expires_at,
        state=ReservationState(record.state),
        settled_ai_turns=record.settled_ai_turns,
        settled_voice_seconds=record.settled_voice_seconds,
    )


def _store_terminal(
    record: UsageReservationRecord, reservation: UsageReservation, at: datetime
) -> None:
    record.state = reservation.state.value
    record.settled_ai_turns = reservation.settled_ai_turns
    record.settled_voice_seconds = reservation.settled_voice_seconds
    record.finalized_at = at


def _validate_operation_units(operation: Operation, ai_turns: int, voice_seconds: int) -> None:
    if type(ai_turns) is not int or type(voice_seconds) is not int:
        raise ValueError("usage units must be integers")
    if operation is Operation.AI_TURN and (ai_turns != 1 or voice_seconds != 0):
        raise ValueError("an AI turn reserves exactly one AI turn and no voice seconds")
    if operation is Operation.VOICE_TRANSCRIPTION and (
        voice_seconds <= 0 or ai_turns not in (0, 1)
    ):
        raise ValueError("voice reserves decoded seconds and at most one interpreted AI turn")
