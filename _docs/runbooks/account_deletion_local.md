# Local account deletion and retention

This runbook covers the local PostgreSQL contract only. It does not establish hosted backup durability, legal retention periods, or deletion by Telegram, payment providers, or model providers.

Run the `retention_deletion_0001` migration before using these operations. Set `PIN_DATABASE_URL` to the intended database and choose an absolute private artifact path outside the database backup rotation. The JSONL artifact contains only a version, event UUID, owner UUID, and deletion time. Do not treat a local file as proof of off-host durability.

The Telegram `/delete_account` command creates an owner-bound, versioned preview with confirm and cancel callbacks. Confirmation rechecks the authenticated owner, current preview ID and version, and its 24-hour expiry inside the transaction. Cancellation changes no account data; stale, expired, or foreign callbacks are rejected. Repeated confirmation is idempotent. Confirmation immediately fences Billing access and work.

Schedule `pin-retention run --artifact C:/absolute/protected/path/deletions.jsonl` on the local host. The command creates an empty replay baseline if needed, fsyncs confirmed deletion tombstones before purging context-owned operational rows, enforces the 30-day text window, and cleans crashed audio using the existing 23-hour file lifecycle. Repeated runs are safe. The composition covers conversation inputs/results, speech transcripts, copied Accountability titles, transient Planning previews, owner-bound command receipts, and platform inbox/jobs/outbox envelopes. Long-lived plans remain intact until account deletion.

After restoring an older database snapshot, run `pin-retention replay --artifact C:/absolute/protected/path/deletions.jsonl` and require exit code 0 before starting Telegram ingress, application, delivery, or speech workers. Replay validates the complete artifact before applying any event and can be repeated. Missing, malformed, or conflicting artifact data fails closed; recover the independently retained artifact before resuming workers. A database-local copy cannot protect against a snapshot taken before export. This is a local operator procedure, not hosted restore orchestration or hosted durability proof.

Private raw speech files use owner-scoped generated names for immediate account cleanup. Existing crash cleanup removes abandoned generated files after 23 hours, leaving scheduler margin within the 24-hour bound. Do not place raw audio or transcripts in the deletion artifact. The confirmation response enters the existing durable reply outbox; delivery workers suppress stale replies after the account is fenced.

Plans remain until the owner confirms deletion. Do not add a separate plan-retention TTL or expiry to deletion markers. Financial evidence and current paid access follow the Billing retention adapter, independently of the 30-day text purge.
