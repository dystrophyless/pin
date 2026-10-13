# Access and usage contract

Billing owns operation entitlement and the period-scoped user-usage ledger. Callers check access for the operation they are about to perform and reserve usage before invoking AI or voice work. Access checks do not imply quota availability: the reservation is the authoritative quota gate.

## Rights and allowances

Access periods use half-open UTC windows: `starts_at <= now < ends_at`. Trial policy v1 lasts 7 days and allows 250 AI turns and 900 decoded voice seconds. Paid policy v1 lasts 30 days and allows 1,000 AI turns and 3,600 decoded voice seconds. Allowances reset per period; unused units do not roll over. A user turn is one AI turn regardless of internal model/provider calls. Voice usage is measured in decoded seconds. Internal provider cost is outside this ledger.

`CheckAccess` allows plan viewing, completion, and cancellation without an active period. AI turns, voice transcription, plan creation, rescheduling, and reminder delivery require an active period. A deleted or unknown account is denied. Quota limits are applied when reserving AI or voice units, not by the entitlement-only access check. Exhausted quota does not itself pause a reminder; expired access does.

## Reservation and finalization

Each reservation is owned by one user and binds a unique request ID to one immutable access period and a fixed requested quantity. A request can reserve at most one AI turn; voice seconds may be reserved at the same time. The caller supplies an aware expiry deadline. The deadline must be after creation and cannot exceed the period end; Billing does not invent a timeout duration. The AI and voice dimensions are checked and reserved together, so neither can be charged when the combined request exceeds either allowance.

The adapter serializes work on the owner account row and the selected period row inside the caller's database transaction. Request IDs are unique per owner. A retry returns the original reservation and period; reusing a request ID with different units or period is rejected. A retry after a period transition cannot create a reservation in the new period. Settlement, refund, and expiry are terminal outcomes: settlement records measured units, refund releases an unsuccessful operation, and expiry releases a reservation whose deadline has passed. Replaying the same successful settlement or refund is idempotent; conflicting settlement data or late work after expiry is rejected.

Callers must keep database transactions short. Commit the reservation before making any external AI/voice call, then settle measured usage or refund a failed call in a separate transaction. Never hold a database transaction or row lock across a provider call. If the process crashes, recover using the same owner and request ID: read/replay the existing reservation, reconcile the external outcome using the caller's durable call/provider receipt, then settle or refund it idempotently. Do not repeat an external call solely because the local settlement result is unknown. If its deadline has passed, expiry is terminal and late settlement cannot charge it.

## Expiry worker integration

`expire_reservations(at, limit)` expires no more than `limit` due reservations, ordered by deadline and row ID, using `FOR UPDATE SKIP LOCKED`. It marks rows expired and records the supplied processing time; skipped locks and later due rows are picked up by later invocations. New reservations also expire due rows in their period before calculating available quota, so a delayed worker does not permanently hold quota.

The method is a bounded batch primitive, not a running scheduler. The application/integration owner must arrange repeated invocations with an aware current time and a positive batch limit, commit each batch, and monitor/retry failed batches. No automatic scheduler or provider-outcome reconciler is part of this change.

## Scope and verification

Trial activation is owned by Billing and runs through `SqlAlchemyAccessUsageStore.activate_trial(StartTrial(...))` on the caller's existing session/transaction. It creates one seven-day period with 250 AI turns and 900 voice seconds. The ingress-established owner and trusted Telegram identity are checked together; one Telegram identity cannot activate for another owner. Concurrent confirmation is serialized on the owner row. A replay returns the original trial fact, while an active paid period is returned unchanged. A minimal owner/Telegram trial marker survives account deletion to prevent a returning identity from receiving a second trial; it does not restore the deleted profile or plans. Account removal must soft-delete the Billing account and preserve periods/reservations so return can restore active paid access with its original usage facts. Reconfirmation clears the soft-delete flag only when an active paid period exists; expired paid periods remain inactive. The caller must commit the same transaction as its onboarding confirmation.

This contract does not cover payment receipts/refunds, provider/model cost accounting, automatic scheduler setup, or broader retention policy.

Run the access unit and PostgreSQL integration tests with a disposable PostgreSQL database configured in `PIN_TEST_BILLING_DATABASE_URL`:

```powershell
uv run --frozen pytest -q tests/test_billing_access.py tests/test_billing_access_postgres.py
```

The PostgreSQL tests exercise concurrent reservations, atomic AI/voice limits, period-bound retries, expiry/refund behavior, and deleted/unknown owners. The unit tests cover the pure entitlement and ledger rules.
