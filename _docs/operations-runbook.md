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
job age; and lists at most 25 dead-job and expired-lease IDs. It also reports process heartbeats and
context-owned summaries for delivery outcomes, provider usage, speech failures, payment orders,
refunds, and expired usage reservations. These projections omit user IDs, Telegram destinations,
job payloads, task text, transcripts, prompts, provider charge IDs, credentials, and free-form error
messages. Correlate a case with the reported opaque job, attempt, call, reservation, or order ID.

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
2. Compare lane counts and the oldest due pending age with the deployment's selected service
   objectives. No queue-age service threshold is selected in the approved product contract.
3. For a stale worker, compare `role`, `worker_id`, and `last_seen_at` with the process manager.
   Restart only after checking whether that worker owns an unexpired lease or a started send.
4. For dead work, inspect the job ID through the owning feature's approved tooling and its safe
   error category. The diagnostic output intentionally omits `last_error`, which may be free text.
5. Treat an unknown Telegram send, invoice, renewal cancellation, refund, or model call as an
   unresolved external outcome. Use the reported correlation ID and the corresponding provider
   record to reconcile it; do not resend or replay merely because the outcome is unknown.
6. An overdue reserved usage hold is a reconciliation signal. Check the reservation ID and owning
   job/provider-call records before deciding whether it can be finalized.
7. For user support, follow the existing configured support route and its owning task. Keep private
   content out of operational notes; include only opaque IDs, timestamps, state counts, and safe
   error categories.

## Limits of this local foundation

This command does not configure host alerts, inspect a remote provider, or prove service behavior
on a deployed host. `restore_and_deletion_progress` is explicitly reported as unavailable because
the retention/deletion consumer and the post-restore deletion replay contract are not implemented
in this local slice. Restore, deletion, alert delivery, provider reconciliation, and actual-host
checks remain required before issue 22 can be accepted as a whole.
