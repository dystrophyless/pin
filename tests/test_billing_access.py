from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pin.contexts.billing.domain.access import (
    AccessAccount,
    AccessDenial,
    AccessPeriod,
    Allowance,
    IdempotencyConflict,
    InvalidTransition,
    Operation,
    PeriodKind,
    PlanPolicy,
    QuotaExceeded,
    ReservationState,
    UsageLedger,
    check_access,
)

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def period(
    kind: PeriodKind, *, start: datetime = NOW, period_id: str | None = None
) -> AccessPeriod:
    policy = PlanPolicy.trial() if kind is PeriodKind.TRIAL else PlanPolicy.paid()
    return AccessPeriod(
        period_id=period_id or kind.value,
        policy=policy,
        starts_at=start,
        ends_at=start + policy.duration,
    )


@pytest.mark.parametrize(
    ("kind", "days", "turns", "voice_seconds"),
    [
        (PeriodKind.TRIAL, 7, 250, 900),
        (PeriodKind.PAID, 30, 1000, 3600),
    ],
)
def test_versioned_policy_has_approved_period_and_allowances(
    kind: PeriodKind, days: int, turns: int, voice_seconds: int
) -> None:
    policy = PlanPolicy.trial() if kind is PeriodKind.TRIAL else PlanPolicy.paid()

    assert policy.duration == timedelta(days=days)
    assert policy.allowance == Allowance(turns, voice_seconds)


@pytest.mark.parametrize("kind", [PeriodKind.TRIAL, PeriodKind.PAID])
def test_access_period_is_active_at_start_but_expired_at_exact_end(kind: PeriodKind) -> None:
    access_period = period(kind)
    account = AccessAccount((access_period,))

    assert check_access(account, Operation.AI_TURN, access_period.starts_at).allowed
    decision = check_access(account, Operation.AI_TURN, access_period.ends_at)
    assert not decision.allowed
    assert decision.denial is AccessDenial.ACCESS_EXPIRED


def test_expired_access_keeps_view_complete_and_cancel_but_pauses_delivery_and_new_work() -> None:
    expired = period(PeriodKind.TRIAL, start=NOW - timedelta(days=8))
    account = AccessAccount((expired,))

    for operation in (Operation.VIEW_PLAN, Operation.COMPLETE_PLAN, Operation.CANCEL_PLAN):
        assert check_access(account, operation, NOW).allowed
    for operation in (
        Operation.CREATE_PLAN,
        Operation.RESCHEDULE_PLAN,
        Operation.AI_TURN,
        Operation.VOICE_TRANSCRIPTION,
        Operation.REMINDER_DELIVERY,
    ):
        assert not check_access(account, operation, NOW).allowed


def test_exhausted_active_quota_does_not_remove_access_or_reminder_eligibility() -> None:
    active = period(PeriodKind.TRIAL)
    account = AccessAccount((active,))
    ledger = UsageLedger(active.period_id, active.policy.allowance, active.ends_at)
    for index in range(active.policy.allowance.ai_turns):
        ledger.reserve(
            request_id=f"turn-{index}",
            period_id=active.period_id,
            ai_turns=1,
            voice_seconds=0,
            at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )

    assert ledger.snapshot().remaining_ai_turns == 0
    assert check_access(account, Operation.AI_TURN, NOW).allowed
    assert check_access(account, Operation.REMINDER_DELIVERY, NOW).allowed
    with pytest.raises(QuotaExceeded):
        ledger.reserve(
            request_id="over-limit",
            period_id=active.period_id,
            ai_turns=1,
            voice_seconds=0,
            at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )


def test_deleted_account_is_ineligible_even_with_active_paid_coverage() -> None:
    account = AccessAccount((period(PeriodKind.PAID),), deleted=True)

    for operation in Operation:
        decision = check_access(account, operation, NOW)
        assert not decision.allowed
        assert decision.denial is AccessDenial.ACCOUNT_DELETED


def test_paid_period_is_authoritative_during_trial_upgrade() -> None:
    account = AccessAccount((period(PeriodKind.TRIAL), period(PeriodKind.PAID, period_id="paid-1")))

    decision = check_access(account, Operation.AI_TURN, NOW)
    assert decision.allowed
    assert decision.period_id == "paid-1"


def test_reserve_is_atomic_across_ai_and_voice_and_never_rolls_into_new_period() -> None:
    trial = UsageLedger("trial-1", Allowance(1, 10), NOW + timedelta(days=7))
    paid = UsageLedger("paid-1", Allowance(1, 10), NOW + timedelta(days=30))
    expires = NOW + timedelta(minutes=5)

    trial.reserve(
        request_id="voice-request",
        period_id="trial-1",
        ai_turns=1,
        voice_seconds=10,
        at=NOW,
        expires_at=expires,
    )
    with pytest.raises(QuotaExceeded):
        trial.reserve(
            request_id="second-request",
            period_id="trial-1",
            ai_turns=1,
            voice_seconds=0,
            at=NOW,
            expires_at=expires,
        )
    assert trial.snapshot().remaining_ai_turns == 0
    assert trial.snapshot().remaining_voice_seconds == 0

    paid.reserve(
        request_id="new-paid-period-request",
        period_id="paid-1",
        ai_turns=1,
        voice_seconds=10,
        at=NOW,
        expires_at=expires,
    )
    assert paid.snapshot().consumed_ai_turns == 0


def test_settlement_is_bound_to_period_and_duplicate_outcomes_are_idempotent() -> None:
    ledger = UsageLedger("paid-period-7", Allowance(2, 100), NOW + timedelta(days=30))
    expiry = NOW + timedelta(minutes=2)
    ledger.reserve(
        request_id="request-1",
        period_id="paid-period-7",
        ai_turns=1,
        voice_seconds=70,
        at=NOW,
        expires_at=expiry,
    )

    settled = ledger.settle(
        request_id="request-1", ai_turns=1, voice_seconds=65, at=NOW + timedelta(seconds=1)
    )
    replay = ledger.settle(
        request_id="request-1", ai_turns=1, voice_seconds=65, at=NOW + timedelta(seconds=2)
    )
    assert replay == settled
    assert ledger.snapshot().consumed_ai_turns == 1
    assert ledger.snapshot().consumed_voice_seconds == 65
    assert ledger.snapshot().remaining_voice_seconds == 35
    with pytest.raises(IdempotencyConflict):
        ledger.settle(request_id="request-1", ai_turns=1, voice_seconds=64, at=NOW)
    with pytest.raises(InvalidTransition):
        ledger.refund(request_id="request-1", at=NOW)
    with pytest.raises(InvalidTransition):
        UsageLedger("next-period", Allowance(2, 100), NOW + timedelta(days=30)).settle(
            request_id="request-1", ai_turns=1, voice_seconds=65, at=NOW
        )


def test_refund_releases_both_dimensions_once_and_cannot_reverse_settlement() -> None:
    ledger = UsageLedger("trial", Allowance(1, 30), NOW + timedelta(days=7))
    ledger.reserve(
        request_id="voice",
        period_id="trial",
        ai_turns=1,
        voice_seconds=30,
        at=NOW,
        expires_at=NOW + timedelta(minutes=3),
    )

    refunded = ledger.refund(request_id="voice", at=NOW + timedelta(seconds=1))
    assert ledger.refund(request_id="voice", at=NOW + timedelta(seconds=2)) == refunded
    assert ledger.snapshot().remaining_ai_turns == 1
    assert ledger.snapshot().remaining_voice_seconds == 30
    with pytest.raises(InvalidTransition):
        ledger.settle(request_id="voice", ai_turns=1, voice_seconds=30, at=NOW)
    ledger.reserve(
        request_id="successful-voice",
        period_id="trial",
        ai_turns=1,
        voice_seconds=30,
        at=NOW,
        expires_at=NOW + timedelta(minutes=3),
    )
    ledger.settle(request_id="successful-voice", ai_turns=1, voice_seconds=30, at=NOW)
    with pytest.raises(InvalidTransition):
        ledger.refund(request_id="successful-voice", at=NOW)


def test_successful_ai_reservation_must_settle_the_user_turn() -> None:
    ledger = UsageLedger("trial", Allowance(1, 30), NOW + timedelta(days=7))
    ledger.reserve(
        request_id="turn",
        period_id="trial",
        ai_turns=1,
        voice_seconds=0,
        at=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )

    with pytest.raises(InvalidTransition):
        ledger.settle(request_id="turn", ai_turns=0, voice_seconds=0, at=NOW)
    assert ledger.refund(request_id="turn", at=NOW).state is ReservationState.REFUNDED


def test_expiry_releases_reservation_and_fences_late_settlement() -> None:
    ledger = UsageLedger("trial", Allowance(1, 20), NOW + timedelta(days=7))
    deadline = NOW + timedelta(minutes=1)
    ledger.reserve(
        request_id="stuck",
        period_id="trial",
        ai_turns=1,
        voice_seconds=20,
        at=NOW,
        expires_at=deadline,
    )

    assert ledger.expire(at=deadline - timedelta(microseconds=1)) == ()
    expired = ledger.expire(at=deadline)
    assert len(expired) == 1
    assert expired[0].state is ReservationState.EXPIRED
    assert ledger.snapshot().remaining_ai_turns == 1
    with pytest.raises(InvalidTransition):
        ledger.settle(request_id="stuck", ai_turns=1, voice_seconds=20, at=deadline)


def test_reservation_cannot_cross_period_end_and_terminal_action_at_end_is_rejected() -> None:
    period_end = NOW + timedelta(days=7)
    ledger = UsageLedger("trial", Allowance(1, 20), period_end)
    with pytest.raises(InvalidTransition):
        ledger.reserve(
            request_id="too-late",
            period_id="trial",
            ai_turns=1,
            voice_seconds=0,
            at=period_end,
            expires_at=period_end + timedelta(minutes=1),
        )

    ledger.reserve(
        request_id="at-boundary",
        period_id="trial",
        ai_turns=1,
        voice_seconds=0,
        at=NOW,
        expires_at=period_end,
    )
    with pytest.raises(InvalidTransition):
        ledger.settle(request_id="at-boundary", ai_turns=1, voice_seconds=0, at=period_end)


def test_duplicate_request_must_keep_its_original_reservation_payload() -> None:
    ledger = UsageLedger("trial", Allowance(3, 30), NOW + timedelta(days=7))
    kwargs = {
        "request_id": "same",
        "period_id": "trial",
        "ai_turns": 1,
        "voice_seconds": 0,
        "at": NOW,
        "expires_at": NOW + timedelta(minutes=1),
    }
    first = ledger.reserve(**kwargs)
    assert ledger.reserve(**kwargs) == first
    with pytest.raises(IdempotencyConflict):
        ledger.reserve(**{**kwargs, "voice_seconds": 1})


def test_invalid_period_and_naive_clock_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        period(PeriodKind.TRIAL, start=datetime(2026, 10, 13))
    ledger = UsageLedger("trial", Allowance(1, 1), NOW + timedelta(days=7))
    with pytest.raises(ValueError):
        ledger.reserve(
            request_id="bad",
            period_id="another-period",
            ai_turns=1,
            voice_seconds=0,
            at=NOW,
            expires_at=NOW + timedelta(seconds=1),
        )


def test_voice_resize_uses_its_own_hold_and_releases_decreased_quota() -> None:
    ledger = UsageLedger("trial", Allowance(2, 100), NOW + timedelta(days=7))
    expiry = NOW + timedelta(minutes=1)
    ledger.reserve(
        request_id="first",
        period_id="trial",
        ai_turns=1,
        voice_seconds=70,
        at=NOW,
        expires_at=expiry,
    )
    ledger.reserve(
        request_id="second",
        period_id="trial",
        ai_turns=0,
        voice_seconds=30,
        at=NOW,
        expires_at=expiry,
    )

    resized = ledger.resize_voice("first", 40, NOW + timedelta(seconds=1))
    assert resized.voice_seconds == 40
    assert ledger.snapshot().remaining_voice_seconds == 30
    ledger.resize_voice("second", 60, NOW + timedelta(seconds=2))
    assert ledger.snapshot().reserved_voice_seconds == 100


def test_voice_resize_preserves_ai_hold_and_same_quantity_is_idempotent() -> None:
    ledger = UsageLedger("trial", Allowance(1, 100), NOW + timedelta(days=7))
    ledger.reserve(
        request_id="combined",
        period_id="trial",
        ai_turns=1,
        voice_seconds=20,
        at=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )

    resized = ledger.resize_voice("combined", 80, NOW + timedelta(seconds=1))
    assert (resized.ai_turns, resized.voice_seconds, resized.created_at, resized.expires_at) == (
        1,
        80,
        NOW,
        NOW + timedelta(minutes=1),
    )
    assert ledger.resize_voice("combined", 80, NOW + timedelta(seconds=2)) == resized
    assert ledger.snapshot().reserved_ai_turns == 1
    assert ledger.snapshot().reserved_voice_seconds == 80


@pytest.mark.parametrize("units", [0, -1, True, 1.5])
def test_voice_resize_rejects_malformed_units_without_state_change(units: int) -> None:
    ledger = UsageLedger("trial", Allowance(1, 100), NOW + timedelta(days=7))
    ledger.reserve(
        request_id="voice",
        period_id="trial",
        ai_turns=0,
        voice_seconds=20,
        at=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )
    before = ledger.reservation("voice")

    with pytest.raises(ValueError):
        ledger.resize_voice("voice", units, NOW + timedelta(seconds=1))
    assert ledger.reservation("voice") == before


def test_voice_resize_rejects_overflow_missing_wrong_kind_and_terminal_holds() -> None:
    ledger = UsageLedger("trial", Allowance(1, 100), NOW + timedelta(days=7))
    expiry = NOW + timedelta(minutes=1)
    ledger.reserve(
        request_id="voice",
        period_id="trial",
        ai_turns=0,
        voice_seconds=90,
        at=NOW,
        expires_at=expiry,
    )
    ledger.reserve(
        request_id="other",
        period_id="trial",
        ai_turns=0,
        voice_seconds=10,
        at=NOW,
        expires_at=expiry,
    )
    before = ledger.reservation("voice")
    with pytest.raises(QuotaExceeded):
        ledger.resize_voice("voice", 91, NOW + timedelta(seconds=1))
    assert ledger.reservation("voice") == before
    with pytest.raises(InvalidTransition):
        ledger.resize_voice("missing", 1, NOW + timedelta(seconds=1))

    ledger.reserve(
        request_id="ai-only",
        period_id="trial",
        ai_turns=1,
        voice_seconds=0,
        at=NOW,
        expires_at=expiry,
    )
    with pytest.raises(InvalidTransition):
        ledger.resize_voice("ai-only", 1, NOW + timedelta(seconds=1))

    ledger.settle(
        request_id="voice",
        ai_turns=0,
        voice_seconds=20,
        at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(InvalidTransition):
        ledger.resize_voice("voice", 30, NOW + timedelta(seconds=3))

    expired_ledger = UsageLedger("trial", Allowance(1, 100), NOW + timedelta(days=7))
    expired_ledger.reserve(
        request_id="expired",
        period_id="trial",
        ai_turns=0,
        voice_seconds=20,
        at=NOW,
        expires_at=expiry,
    )
    expired_ledger.expire(at=expiry)
    with pytest.raises(InvalidTransition):
        expired_ledger.resize_voice("expired", 30, expiry)
