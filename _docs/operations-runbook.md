# Local operations runbook

## Read a redacted snapshot

From the repository root, run:

```powershell
uv run python -m pin.entrypoints.operations
```

The command reads `PIN_DATABASE_URL`, performs a read-only database probe, and prints one JSON
snapshot. It also reports host CPU count and short-sample CPU busy percentage, available system
memory on Windows, and free disk space on the current volume. A nonzero exit means the database
could not be read; the output includes only the exception class, never its message or connection
details.

The snapshot groups jobs by application, speech, and delivery lane; reports the oldest due pending
job age and up to 25 oldest due pending job IDs; and lists at most 25 dead-job and expired-lease IDs.
The pending-ID sample is a bounded investigation aid, not a queue capacity or fullness measure. It
also reports process heartbeats and
context-owned summaries for delivery outcomes, provider usage, speech failures, payment orders,
refunds, expired usage reservations, and retention deletion state counts. Retention reports the
oldest confirmed deletion awaiting export, its opaque request ID, and the count plus up to 25 opaque
event IDs for tombstones that have not been exported. The restore status remains `unavailable`: this
snapshot does not inspect backup or host restore progress. These projections omit owner IDs, Telegram destinations,
job payloads, task text, transcripts, prompts, provider charge IDs, credentials, and free-form error
messages. For an active paid period, `active_paid_period_traces` links its opaque period ID to the
receipt's internal order ID and a bounded set of usage reservation IDs with state counts. A paid
period without its expected receipt link is an invariant to investigate; `receipt_linked: null`
means the receipt candidate sample exceeded its cap and the link result is unavailable. This
projection does not compare Stars with AI/voice usage units or perform a financial reconciliation. Correlate a case
with the reported opaque job, attempt, call, reservation, period, or order ID.

The platform `recovery` projection reports the recovery-gate state and, when present, opaque
snapshot/ledger IDs and fence/release times. It deliberately omits reconciliation evidence hashes
and file paths. `unavailable` means the gate row is not visible; the retention projection's
`restore_status` remains unavailable because restore evidence is owned by the platform recovery
gate, not the retention context.

Host `cpu_busy_percent` and `cpu_available_cores` are short-sample local readings. They do not
measure Lightsail burst balance. To inspect burst capacity, open the instance in the AWS Lightsail
console, select **Metrics**, then **CPU overview**; review **Remaining CPU burst capacity** together
with CPU utilization. The console supports percentage and minutes views. See the [AWS Lightsail CPU
burst monitoring guide](https://docs.aws.amazon.com/lightsail/latest/userguide/monitoring-cpu-burst-capacity.html).

Webhook mode exposes `GET /healthz` for process liveness and `GET /readyz` for a database-read
check. Polling and application, delivery, and speech workers write a heartbeat every 15 seconds;
they mark a clean stop when possible. A heartbeat older than 45 seconds is reported as `stale`.
That interval is a technical detection default (three missed beats), not a product availability
target. Readiness bounds its database probe at two seconds and returns the same dependency-only 503
for query errors and timeouts. Crashes cannot record a clean stop, so their heartbeat becomes stale.
The operator command does not send alerts or contact anyone.

## Triage

1. Check database readiness and disk/memory/CPU readings first. If the database is unavailable,
   restore connectivity before interpreting missing queue or context data.
2. Inspect the oldest due pending job IDs and lane counts with the oldest due age. The hosted health
   timer signals when this global age exceeds its configured 300-second warning; this is an
   operational warning, not a selected product service objective. The IDs identify work for
   feature-owner investigation; do not infer queue capacity from the sample.
3. For a stale worker, compare `role`, `worker_id`, and `last_seen_at` with the process manager.
   Restart only after checking whether that worker owns an unexpired lease or a started send.
4. For dead work, inspect the job ID through the owning feature's approved tooling and its safe
   error category. The diagnostic output intentionally omits `last_error`, which may be free text.
5. For a paid-period trace, follow `period_id` to its linked `order_id` and reservation/request IDs.
   If `receipt_linked` is false for an active paid period, verify the persisted linkage read-only
   and escalate to the billing owner; if it is null, the bounded sample cannot determine linkage.
   Do not create a receipt, activate access, or compare Stars with usage units based on this trace.
   Treat an unknown Telegram send, invoice, renewal
   cancellation, refund, or model call as an
   unresolved external outcome. Use the reported correlation ID and the corresponding provider
   record to reconcile it; do not resend or replay merely because the outcome is unknown.
6. An overdue reserved usage hold is a reconciliation signal. Check the reservation ID and owning
   job/provider-call records before deciding whether it can be finalized.
7. If `recovery.state` is `fenced`, keep runtime workers stopped and follow the
   [hosted recovery runbook](runbooks/hosted_recovery.md) until reconciliation is complete and the
   gate is released. `ready` reports only the
   persisted gate state, not proof that an external backup or its contents were verified.
8. For user support, follow the existing configured support route and its owning task. Keep private
   content out of operational notes; include only opaque IDs, timestamps, state counts, and safe
   error categories.

## Limits of this local foundation

The host timer emits redacted health signals to the local system journal; it does not page
externally. Deletion progress reflects persisted request and tombstone rows only; it does not prove
that an artifact exists or that owner data was purged. Recovery-gate state reports the runtime fence
and reconciliation marker, not backup contents or restore correctness. Actual provider reconciliation
and owner-data verification remain separate checks before issue 22 can be accepted as a whole.
