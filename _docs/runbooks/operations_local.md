# Local operations and failure investigation

This page records the manual Windows foundation. For the deployed health timer, current bounded
correlation fields and recovery-gate investigation, use the [current operations runbook](../operations-runbook.md).

This runbook applies to the Windows local-development runtime. The current command takes one read-only snapshot; it does not repair jobs, retry external actions, deliver alerts, or prove a backup restore. It connects to the database configured for the current process. Confirm privately that `PIN_DATABASE_URL` targets the intended local database before running it; never print or paste that value.

## Take a snapshot

From the repository root, with the project's frozen environment available:

```powershell
uv run --frozen pin-operations
```

The command prints one JSON object and exits `0` when the database snapshot succeeds. It exits `2` when configuration or database access prevents a complete snapshot. `--help` is available; there are no snapshot, reset, retry, or repair flags. The report is useful for investigation, not an alert integration. Capture the timestamp, revision, exit code, and only the redacted fields needed for the incident record.

## Read the snapshot and investigate

Start with `generated_at` and `database_ready`. If `database_ready` is false, the host section may still be present, but lane and context data are absent. Check that the intended local PostgreSQL service is running, the configured database is reachable, and the local schema is at the expected migration head. `database_error` is diagnostic text on configuration failure and an exception class on snapshot failure; neither is a remediation. Review and sanitize it before sharing. Correct the local environment through its normal configuration path and rerun the snapshot; do not include connection strings in notes.

`workers` contains up to 100 recent records with `worker_id`, `role`, `status`, `started_at`, and `last_seen_at`. A worker is `stopped` when `stopped_at` is recorded, `stale` when its last heartbeat is more than `heartbeat_stale_after_seconds` old, otherwise `running`. The current threshold is 45 seconds (three nominal 15-second heartbeats). A role absent from the bounded list or a missing lane count is not proof that it is healthy. Check the matching local process/service and its redacted startup logs, then use the documented normal start procedure. Do not alter job rows to make the heartbeat look healthy.

`lanes` reports counts by current job `status` for `application`, `speech`, and `delivery`. `oldest_due_pending_seconds` is the age of the oldest globally due pending job, or `null` when none exists; it is not a per-lane age. `dead_job_ids` lists up to 25 most recently terminal `dead` jobs. `expired_lease_job_ids` lists up to 25 `leased` jobs whose lease has expired. Use these opaque job IDs to inspect the matching redacted application/worker error and the owning context's persisted state. Check whether the corresponding worker is live and whether the queue is accumulating. The snapshot does not expose job payloads or retry history. Never reset a queue, manually claim delivery, or replay a job based only on these counts.

The context projections are ownership-scoped and omit user content:

| JSON path | What to inspect |
| --- | --- |
| `contexts.delivery.outcomes`, `unknown_attempt_ids`, `started_attempts` | Delivery outcomes combine reminder and Accountability attempt records. `unknown` means the send result is uncertain; `started` includes attempt IDs and start times. Correlate an opaque attempt ID with its persisted attempt and provider-side evidence through the authorized operator path. Do not resend an unknown attempt or record delivery as confirmed without confirmation. |
| `contexts.provider.states`, `unknown_call_ids`, `started_call_ids`, `reported_tokens`, `reported_cost_usd` | `unknown` or lingering `started` calls need provider/accounting reconciliation using the opaque call ID. Token and USD totals include only reported calls and are not a complete spend estimate while calls are unknown or started. Do not repeat a model call to resolve uncertainty. |
| `contexts.speech.states`, `failed_job_ids` | Compare failed speech job IDs with `lanes.speech` and the redacted worker error. Counts summarize request states; no transcript, language, or request key is exposed. Do not retrieve transcripts for routine diagnosis. |
| `contexts.billing.reservation_states`, `overdue_reservation_ids` | An overdue ID is a `reserved` usage reservation whose expiry has passed. Reconcile the owner account and request lifecycle through Billing's authorized support path before deciding whether a reservation is truly leaked. Never edit balances or release a reservation by hand. |
| `contexts.billing.invoice_states`, `cancellation_states`, `uncertain_order_ids` | `sending` or `unknown` invoice/cancellation state is an uncertain external action. Reconcile the exact persisted order and available provider evidence first; do not retry invoice delivery or cancellation blindly. |
| `contexts.billing.refund_states`, `uncertain_refund_order_ids` | `unknown` refund state requires authorized provider and persisted-record reconciliation. Follow [payment support and refund operations](payments_local.md); do not rerun a refund command on an unknown result. |
| `contexts.billing.receipt_count`, `receipt_total_stars` | Aggregate receipt facts only; these do not establish actual provider-payment testing or legal/financial reconciliation. |
| `contexts.retention` or `restore_and_deletion_progress` | These are the same retention progress projection. See the retention steps below. |

For `oldest_due_pending_seconds`, an increasing value indicates that eligible work is waiting longer. Compare repeat snapshots and inspect the lane heartbeats and bounded status counts. This field is global, so it cannot identify the affected lane by itself. The report defines no automatic threshold or host alert; agree an operational response with the runtime owner instead of treating an invented threshold as configured behavior.

For `host`, `cpu_busy_percent` is a short sample, `cpu_available_cores` is derived from that sample, and `memory_total_bytes` / `memory_available_bytes` may be `null` if the platform query is unavailable. Compare `disk_free_bytes` against `disk_total_bytes`; both describe the current working-directory volume. If disk space is low, identify the responsible local files/logs and use their owner-approved retention procedure. Do not delete database files, backups, deletion artifacts, or evidence packets to free space. If either disk value is `null` and `disk_error` contains an exception class, the disk measurement failed; inspect the volume through the host's normal tools and rerun this read-only snapshot. Do not infer free space from an unavailable measurement. Resource values are point-in-time measurements, not alerts.

Retention fields are `request_state_counts`, `oldest_pending_seconds`, `pending_request_ids`, `unexported_tombstone_count`, `unexported_tombstone_event_ids`, and `restore_status`. `pending_export` work and unexported tombstones require the normal retention/export procedure described in [account deletion and retention](account_deletion_local.md); the IDs are opaque correlation values. The current `restore_status` is always `unavailable`: no restore monitor or restore verification is wired into this snapshot. After restoring a snapshot, follow the retention runbook's artifact replay gate before starting workers. Never interpret `unavailable` as a successful restore or as a deletion failure.

## Safe investigation sequence

1. Save the timestamp, revision, command exit code, and affected opaque IDs in the restricted incident record. Keep a second snapshot only when it helps establish whether counts or ages are moving.
2. Check the worker role and its recent redacted startup/error output. Avoid copying environment dumps or full logs into tickets.
3. Follow each ID to the owning context's persisted state and documented operator procedure. Preserve the distinction between confirmed, failed, and unknown external outcomes.
4. Escalate mismatched payment/usage facts to an authorized Billing operator and deletion/export/restore questions to the retention operator. Record what remains uncertain.
5. Resume work only through the owning worker/application procedure after its persisted state and external outcome are understood. Record the evidence that supports that decision.

Do not reset queues, manually retry unknown sends/invoices/cancellations/refunds, delete business state, or claim delivery/payment success from a process exit code. Logs and incident notes must not contain prompts, task titles/details, transcripts, audio, tokens, secrets, database URLs, Telegram destinations, or raw provider payloads. Prefer the snapshot's opaque IDs and aggregate counts; restrict owner-to-ID mapping to the authorized support path.

## Scope boundary

The implementation currently offers a manual local snapshot only. It has no configured alert transport, threshold policy, off-host backup/restore monitor, or operator paging route. Host alerts and off-host restore verification remain deferred; this document does not claim those controls exist or that planned failure scenarios were injected on a live host.
