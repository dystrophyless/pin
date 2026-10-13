# Durable platform work

`pin.platform` owns transport receipts, worker scheduling, conversation serialization, and integration-event delivery. It does not own plan, reminder, access, or usage policy.

## Transaction boundaries

- `receive_update` writes the unique `(bot_id, update_id)` inbox row and its deduplicated job in the caller's transaction. The transport may acknowledge only after that transaction commits.
- `enqueue_job` and `add_outbox_event` join the business command's Unit of Work. Do not call providers, Telegram, or speech recognition while holding that transaction.
- A consumer calls `consume_event` in the same Unit of Work as its effects. Applied receipts, the consumer version cursor, and handler writes commit together. A failed transaction rolls all three back. Version gaps stay pending and can be inspected with `pending_event_receipts` or replayed with `reconcile_pending_events`.
- A worker calls `record_job_effect_once` before applying a replayable business effect, in the same transaction as that effect. A retry receives the original result after a crash between the business commit and job completion.

## Claims and ordering

Workers claim one due job from their own `application`, `speech`, or `delivery` lane with `FOR UPDATE SKIP LOCKED`. The claim transaction is short; handler work runs after it commits. Claim tokens fence renewal, completion, failure, and effect transactions. Conversation jobs also carry a database-assigned owner sequence and a monotonically increasing fencing generation. The next sequence remains blocked while an earlier conversation job is pending, leased, or retrying. Delivery jobs do not take the conversation lease, so notification dispatch can proceed during text or speech work.

Before writing a business effect, action receipt, continuation job, or outbox event, pass the `JobClaim` to the corresponding platform API so the live token is locked and checked in that same transaction. A reclaimed worker receives `LeaseLost`; it must discard its result. External sends cannot be made exactly once by a database lease. Delivery handlers retain their own attempt and unknown-outcome contract.

Failed jobs use bounded exponential retry and retain the last error. Once `max_attempts` is reached the job becomes `dead` for operator inspection; terminal jobs do not block later conversation sequences.

## Local PostgreSQL checks

The real-database tests are in `tests/test_platform_work_postgres.py`. Set `PIN_TEST_PLATFORM_DATABASE_URL` to a fresh disposable PostgreSQL database and run:

```powershell
uv run pytest tests/test_platform_work_postgres.py -q
```

The scenarios cover concurrent duplicate receipt and claims, expiry/reclaim fencing, committed-effect replay, lease renewal, speech/text ordering across owners, independent delivery, poison-job termination, out-of-order event recovery, and consumer-transaction rollback.
