"""SQLAlchemy implementation of the atomic owner-scoped usage boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.application.contracts import (
    AccessNoticeQuietPolicy,
    AccessUsageStore,
    ActivatePaidPeriod,
    CheckAccess,
    CheckAccessNotice,
    ReadSubscriptionStatus,
    ReserveUsage,
    ReserveUsageResult,
    ResizeVoiceUsage,
    RestoreReturningPaidAccess,
    RetainBillingIdentity,
    StartTrial,
    SubscriptionStatus,
    TrialActivationResult,
    TrialActivationStatus,
)
from pin.contexts.billing.domain.access import (
    AccessAccount,
    AccessDecision,
    AccessNoticeAuthorization,
    AccessNoticeKind,
    AccessNoticeStatus,
    AccessPeriod,
    AccountLifecycle,
    Allowance,
    InvalidTransition,
    Operation,
    PeriodKind,
    PlanPolicy,
    ReservationState,
    UsageLedger,
    UsageReservation,
    authorize_access_notice,
)
from pin.contexts.billing.domain.access import (
    check_access as decide_access,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessExpiryNoticeRecord,
    AccessPeriodRecord,
    RetainedBillingIdentityRecord,
    TrialActivationRecord,
    UsageReservationRecord,
)
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event


class SqlAlchemyAccessUsageStore(AccessUsageStore):
    """Use the caller's transaction; account and period row locks serialize usage."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def retain_owner_for_return(self, command: RetainBillingIdentity) -> bool:
        """Retain only the actor link needed to restore already-earned access."""
        _require_aware(command.at)
        if type(command.telegram_user_id) is not int or command.telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be a positive integer")

        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .with_for_update()
        )
        if account is None:
            return False

        prior_trial = await self._session.get(TrialActivationRecord, command.owner_id)
        paid_period = await self._session.scalar(
            select(AccessPeriodRecord.period_id)
            .where(
                AccessPeriodRecord.owner_id == command.owner_id,
                AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                AccessPeriodRecord.starts_at <= command.at,
                AccessPeriodRecord.ends_at > command.at,
            )
            .limit(1)
        )
        if prior_trial is None and paid_period is None:
            return False
        if prior_trial is not None and prior_trial.telegram_user_id != command.telegram_user_id:
            raise InvalidTransition("owner does not match the retained trial identity")

        by_owner = await self._session.get(RetainedBillingIdentityRecord, command.owner_id)
        by_actor = await self._session.scalar(
            select(RetainedBillingIdentityRecord).where(
                RetainedBillingIdentityRecord.telegram_user_id == command.telegram_user_id
            )
        )
        if by_owner is not None and by_owner.telegram_user_id != command.telegram_user_id:
            raise InvalidTransition("owner already retains a different Telegram identity")
        if by_actor is not None and by_actor.owner_id != command.owner_id:
            raise InvalidTransition("Telegram identity already retains another owner")
        if by_owner is None:
            await self._session.execute(
                pg_insert(RetainedBillingIdentityRecord)
                .values(
                    owner_id=command.owner_id,
                    telegram_user_id=command.telegram_user_id,
                    retained_at=command.at,
                )
                .on_conflict_do_nothing()
            )
            by_actor = await self._session.scalar(
                select(RetainedBillingIdentityRecord).where(
                    RetainedBillingIdentityRecord.telegram_user_id
                    == command.telegram_user_id
                )
            )
            if by_actor is None or by_actor.owner_id != command.owner_id:
                raise InvalidTransition("Telegram identity already retains another owner")
        return True

    async def resolve_returning_owner(self, telegram_user_id: int) -> UUID | None:
        """Resolve a minimal retained actor link before ingress allocates a new owner."""
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be a positive integer")
        retained = await self._session.scalar(
            select(RetainedBillingIdentityRecord.owner_id).where(
                RetainedBillingIdentityRecord.telegram_user_id == telegram_user_id
            )
        )
        trial_owner = await self._session.scalar(
            select(TrialActivationRecord.owner_id).where(
                TrialActivationRecord.telegram_user_id == telegram_user_id
            )
        )
        if retained is not None and trial_owner is not None and retained != trial_owner:
            raise InvalidTransition("retained billing identity conflicts with trial history")
        return retained if retained is not None else trial_owner

    async def restore_returning_paid_access(
        self, command: RestoreReturningPaidAccess
    ) -> bool:
        """Restore only active paid coverage for the matching retained Telegram actor."""
        _require_aware(command.at)
        if type(command.telegram_user_id) is not int or command.telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be a positive integer")
        retained_owner = await self._session.scalar(
            select(RetainedBillingIdentityRecord.owner_id).where(
                RetainedBillingIdentityRecord.telegram_user_id == command.telegram_user_id
            )
        )
        if retained_owner != command.owner_id:
            return False
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .with_for_update()
        )
        if account is None or account.deleted_at is None:
            return False
        paid = await self._session.scalar(
            select(AccessPeriodRecord.period_id)
            .where(
                AccessPeriodRecord.owner_id == command.owner_id,
                AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                AccessPeriodRecord.starts_at <= command.at,
                AccessPeriodRecord.ends_at > command.at,
            )
            .limit(1)
            .with_for_update()
        )
        if paid is None:
            return False
        account.deleted_at = None
        return True

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
        await self._publish_period_activated(
            command.owner_id,
            period_id=period_id,
            kind=PeriodKind.TRIAL,
            at=command.at,
        )
        return TrialActivationResult(
            TrialActivationStatus.ACTIVATED, period_id, command.at, ends_at
        )

    async def activate_paid_period(self, command: ActivatePaidPeriod) -> bool:
        """Persist a provider-confirmed period once; the caller validates payment."""
        _require_aware(command.starts_at)
        _require_aware(command.ends_at)
        policy = PlanPolicy.paid()
        period = AccessPeriod(
            command.period_id,
            policy,
            command.starts_at,
            command.ends_at,
        )
        if period.ends_at - period.starts_at != policy.duration:
            raise ValueError("paid access period must match the configured 30-day policy")

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
        if account is None or account.deleted_at is not None:
            raise InvalidTransition("paid coverage cannot be added to a deleted account")

        existing = await self._session.scalar(
            select(AccessPeriodRecord)
            .where(
                AccessPeriodRecord.owner_id == command.owner_id,
                AccessPeriodRecord.period_id == command.period_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if (
                existing.policy_kind != PeriodKind.PAID.value
                or existing.starts_at != period.starts_at
                or existing.ends_at != period.ends_at
                or existing.policy_version != policy.version
            ):
                raise InvalidTransition("paid period identifier was reused with different facts")
            return False

        overlap = await self._session.scalar(
            select(AccessPeriodRecord.period_id).where(
                AccessPeriodRecord.owner_id == command.owner_id,
                AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                AccessPeriodRecord.starts_at < period.ends_at,
                AccessPeriodRecord.ends_at > period.starts_at,
            )
        )
        if overlap is not None:
            raise InvalidTransition("paid access periods cannot overlap")

        self._session.add(
            AccessPeriodRecord(
                owner_id=command.owner_id,
                period_id=period.period_id,
                policy_kind=PeriodKind.PAID.value,
                policy_version=policy.version,
                starts_at=period.starts_at,
                ends_at=period.ends_at,
                ai_turn_limit=policy.allowance.ai_turns,
                voice_second_limit=policy.allowance.voice_seconds,
            )
        )
        await self._publish_period_activated(
            command.owner_id,
            period_id=period.period_id,
            kind=PeriodKind.PAID,
            at=period.starts_at,
        )
        return True

    async def read_subscription_status(self, query: ReadSubscriptionStatus) -> SubscriptionStatus:
        _require_aware(query.at)
        lifecycle = await self._account_lifecycle(query.owner_id, lock=False)
        period_rows = list(
            (
                await self._session.scalars(
                    select(AccessPeriodRecord)
                    .where(AccessPeriodRecord.owner_id == query.owner_id)
                    .order_by(AccessPeriodRecord.starts_at.desc(), AccessPeriodRecord.period_id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        active_rows = await self._active_periods(query.owner_id, query.at, lock=False)
        selected = (
            active_rows[0]
            if active_rows
            else next(
                (row for row in period_rows if row.starts_at > query.at.astimezone(UTC)),
                period_rows[0] if period_rows else None,
            )
        )
        trial_used = (await self._session.get(TrialActivationRecord, query.owner_id)) is not None
        if selected is None:
            return SubscriptionStatus(
                query.owner_id,
                lifecycle,
                None,
                None,
                None,
                None,
                False,
                trial_used,
                None,
                None,
                None,
                None,
            )

        ledger, _ = await self._ledger(selected, lock=False)
        ledger.expire(at=query.at)
        usage = ledger.snapshot()
        period = _period_from_record(selected)
        is_active = lifecycle is AccountLifecycle.PRESENT and period.is_active(query.at)
        return SubscriptionStatus(
            owner_id=query.owner_id,
            account_lifecycle=lifecycle,
            period_id=period.period_id,
            period_kind=period.policy.kind,
            starts_at=period.starts_at,
            ends_at=period.ends_at,
            active=is_active,
            trial_used=trial_used,
            ai_turn_limit=period.policy.allowance.ai_turns,
            ai_turns_remaining=usage.remaining_ai_turns,
            voice_seconds_limit=period.policy.allowance.voice_seconds,
            voice_seconds_remaining=usage.remaining_voice_seconds,
        )

    async def enqueue_due_access_notices(self, *, at: datetime, limit: int = 100) -> int:
        """Append at most one current-period warning per owner in this caller's UoW."""
        _require_aware(at)
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        at_utc = at.astimezone(UTC)
        candidates = tuple(
            (
                await self._session.scalars(
                    select(AccessPeriodRecord.owner_id)
                    .where(
                        AccessPeriodRecord.starts_at <= at_utc,
                        AccessPeriodRecord.ends_at > at_utc,
                        AccessPeriodRecord.ends_at <= at_utc + timedelta(hours=24),
                    )
                    .distinct()
                    .order_by(AccessPeriodRecord.owner_id)
                    .limit(limit)
                )
            ).all()
        )
        created = 0
        for owner_id in candidates:
            account = await self._session.scalar(
                select(AccessAccountRecord)
                .where(AccessAccountRecord.owner_id == owner_id)
                .execution_options(populate_existing=True)
                .with_for_update(skip_locked=True)
            )
            if account is None or account.deleted_at is not None:
                continue
            active = await self._active_periods(owner_id, at_utc, lock=True)
            if not active:
                continue
            period_row = active[0]
            period = _period_from_record(period_row)
            remaining = period.ends_at - at_utc
            if remaining <= timedelta(0) or remaining > timedelta(hours=24):
                continue
            kind = (
                AccessNoticeKind.EXPIRY_1H
                if remaining <= timedelta(hours=1)
                else AccessNoticeKind.EXPIRY_24H
            )
            existing = await self._session.scalar(
                select(AccessExpiryNoticeRecord)
                .where(
                    AccessExpiryNoticeRecord.owner_id == owner_id,
                    AccessExpiryNoticeRecord.period_id == period.period_id,
                    AccessExpiryNoticeRecord.kind == kind.value,
                )
                .with_for_update()
            )
            if existing is not None:
                continue
            notice = AccessExpiryNoticeRecord(
                owner_id=owner_id,
                period_id=period.period_id,
                kind=kind.value,
                due_at=period.ends_at
                - timedelta(hours=24 if kind is AccessNoticeKind.EXPIRY_24H else 1),
                emitted_at=at_utc,
            )
            self._session.add(notice)
            await self._session.flush()
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"pin.billing.access-notice:{owner_id}:{period.period_id}:{kind.value}",
                    ),
                    event_type="billing.access_notice.due",
                    schema_version=1,
                    aggregate_id=notice.id,
                    aggregate_version=1,
                    owner_id=owner_id,
                    occurred_at=at_utc,
                    payload={
                        "notice_id": str(notice.id),
                        "period_id": period.period_id,
                    },
                ),
            )
            created += 1
        return created

    async def authorize_access_notice(
        self,
        command: CheckAccessNotice,
        *,
        quiet_policy: AccessNoticeQuietPolicy,
    ) -> AccessNoticeAuthorization:
        _require_aware(command.at)
        if not command.period_id.strip() or len(command.period_id) > 128:
            raise ValueError("period_id must contain 1 to 128 characters")
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        current_periods = await self._active_periods(command.owner_id, command.at, lock=True)
        notice = await self._session.scalar(
            select(AccessExpiryNoticeRecord)
            .where(
                AccessExpiryNoticeRecord.id == command.notice_id,
                AccessExpiryNoticeRecord.owner_id == command.owner_id,
                AccessExpiryNoticeRecord.period_id == command.period_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if notice is None:
            return _skipped_access_notice(AccessNoticeKind.EXPIRY_24H, command.at)
        try:
            notice_kind = AccessNoticeKind(notice.kind)
        except ValueError as error:
            raise InvalidTransition("stored expiry notice kind is invalid") from error
        if (
            account is None
            or account.deleted_at is not None
            or notice is None
            or notice.emitted_at is None
        ):
            return _skipped_access_notice(notice_kind, command.at)

        if not current_periods:
            return _skipped_access_notice(notice_kind, command.at)
        current = _period_from_record(current_periods[0])
        if current.period_id != command.period_id:
            return _skipped_access_notice(notice_kind, command.at)

        quiet_until = await quiet_policy.quiet_until(command.owner_id, command.at)
        return authorize_access_notice(
            current,
            notice_kind,
            at=command.at,
            quiet_until=quiet_until,
        )

    async def _publish_period_activated(
        self,
        owner_id: UUID,
        *,
        period_id: str,
        kind: PeriodKind,
        at: datetime,
    ) -> None:
        aggregate_id = uuid5(NAMESPACE_URL, f"pin.billing.period:{owner_id}:{period_id}")
        await add_outbox_event(
            self._session,
            EventEnvelope(
                id=uuid5(NAMESPACE_URL, f"pin.billing.period.activated:{owner_id}:{period_id}"),
                event_type="billing.access_period.activated",
                schema_version=1,
                aggregate_id=aggregate_id,
                aggregate_version=1,
                owner_id=owner_id,
                occurred_at=at.astimezone(UTC),
                payload={"period_id": period_id, "period_kind": kind.value},
            ),
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

    async def lock_access(self, query: CheckAccess) -> AccessDecision:
        """Fence account and entitlement mutations through the caller's authorization commit."""
        if query.at.tzinfo is None or query.at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        account_record = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == query.owner_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        period_records = await self._session.scalars(
            select(AccessPeriodRecord)
            .where(AccessPeriodRecord.owner_id == query.owner_id)
            .order_by(AccessPeriodRecord.period_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        account = AccessAccount(
            tuple(_period_from_record(row) for row in period_records),
            deleted=account_record is None or account_record.deleted_at is not None,
        )
        return decide_access(account, query.operation, query.at)

    async def account_lifecycle(self, owner_id: UUID) -> AccountLifecycle:
        return await self._account_lifecycle(owner_id, lock=False)

    async def lock_account_lifecycle(self, owner_id: UUID) -> AccountLifecycle:
        return await self._account_lifecycle(owner_id, lock=True)

    async def _account_lifecycle(self, owner_id: UUID, *, lock: bool) -> AccountLifecycle:
        query = (
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == owner_id)
            .execution_options(populate_existing=True)
        )
        account_record = await self._session.scalar(query.with_for_update() if lock else query)
        if account_record is None:
            return AccountLifecycle.MISSING
        if account_record.deleted_at is not None:
            return AccountLifecycle.DELETED
        return AccountLifecycle.PRESENT

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

    async def resize_voice_usage(self, command: ResizeVoiceUsage) -> UsageReservation:
        if command.at.tzinfo is None or command.at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        if type(command.voice_seconds) is not int or command.voice_seconds <= 0:
            raise ValueError("voice_seconds must be a positive integer")

        record = await self._reservation_for_update(command.owner_id, command.request_id)
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .execution_options(populate_existing=True)
        )
        active = await self._active_periods(command.owner_id, command.at, lock=False)
        access = decide_access(
            AccessAccount(
                tuple(_period_from_record(row) for row in active),
                deleted=account is None or account.deleted_at is not None,
            ),
            Operation.VOICE_TRANSCRIPTION,
            command.at,
        )
        if not access.allowed:
            raise InvalidTransition("voice entitlement is not currently active")

        period_row = await self._period_for_update(command.owner_id, record.period_id)
        if period_row is None:
            raise InvalidTransition("reservation period no longer exists")
        ledger, _ = await self._ledger(period_row, lock=True)
        resized = ledger.resize_voice(command.request_id, command.voice_seconds, command.at)
        record.reserved_voice_seconds = resized.voice_seconds
        return resized

    async def get_usage_reservation(
        self, owner_id: UUID, request_id: str
    ) -> UsageReservation | None:
        record = await self._session.scalar(
            select(UsageReservationRecord)
            .where(
                UsageReservationRecord.owner_id == owner_id,
                UsageReservationRecord.request_id == request_id,
            )
            .execution_options(populate_existing=True)
        )
        return _reservation_from_record(record) if record is not None else None

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
            .execution_options(populate_existing=True)
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
            .execution_options(populate_existing=True)
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
            .execution_options(populate_existing=True)
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
            .execution_options(populate_existing=True)
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


def _skipped_access_notice(kind: AccessNoticeKind, at: datetime) -> AccessNoticeAuthorization:
    return AccessNoticeAuthorization(AccessNoticeStatus.SKIP, kind, None, at)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")


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
