# Composed recovery-state preservation — 2026-10-18

**Status: PASS for the local synthetic PostgreSQL scenarios below; not hosted nonzero-event proof or complete disaster-recovery acceptance.** The run began from `84825177e2efad974c8aa7c44836dc97bb65258f` with a test-only extension to `tests/test_retention_restore_postgres.py` in the working tree. During the two isolated test processes, repository HEAD advanced to `532211a941391cc981f95d34975618a77a033388` for documentation-only changes; the recovery runtime, migrations, and test file did not change during the run.

## Verification

On the dedicated PostgreSQL 18.6 test cluster, both existing `tests/test_retention_restore_postgres.py` cases passed independently, each with a fresh source/target database pair:

| Case | Result | Pytest duration |
| --- | --- | ---: |
| `test_dump_restore_replays_deletion_before_recovering_started_send` | 1 passed; 0 failures, errors, or skips | 28.246 s |
| `test_dump_restore_reconciles_send_absent_from_snapshot_before_release` | 1 passed; 0 failures, errors, or skips | 20.390 s |

The absent-send case now snapshots representative persisted recovery state alongside its existing lost-window scenario: open, completed, and cancelled plan occurrences; a synthetic Stars-style receipt and confirmed refund record; and an application job claimed into a valid leased state. It compares the source and restored target values, including plan ownership/state, refund receipt/status/timestamps, and the leased job's owner, status, attempt, worker, token, and lease expiry.

The same case still verifies the cross-boundary behavior: no delivery attempt exists in the snapshot; a synthetic post-snapshot send produces an `unknown` attempt; another synthetic owner is deleted after the snapshot and its newer deletion artifact is replayed while the target is fenced; the deleted plans remain absent while the approved trial/paid identity survives; incomplete usage evidence prevents gate release; complete synthetic observations permit release; and the restored due reminder is processed with zero transport sends while its attempt remains `unknown`. No Telegram, payment, model, or provider API was called.

The run's guarded cleanup removed only its four manifest-owned test databases, used no force option, and found no active sessions in them. The post-cleanup catalog matched the exact 174-database baseline; the protected database set remained present. A separate native packet from the earlier two-case run remains preserved.

After the native run, the two result-map conversions were rewritten as explicitly typed dictionary comprehensions to satisfy scoped mypy. This preserves the same row-to-key/value mapping; scoped mypy, Ruff, compilation, and diff checks passed afterward. PostgreSQL was not rerun for this type-only adjustment.

## How this combines with hosted evidence

The [hosted recovery record](hosted-recovery-20261018.md) proves a separate rehearsal using the deployed restore tooling and genuine hosted backup artifacts, including fenced startup, reconciliation, release/check, and guarded cleanup for its observed zero-event window. This local composed test adds nonzero synthetic post-snapshot deletion and unknown-send coverage plus historical plan, refund, and lease preservation. Together they do not show nonzero hosted/provider history or that provider records were complete.

This is bounded isolated restore evidence, not proof of a 24-hour-aged snapshot, a whole-host failure/replacement, live cutover, end-user recovery, legal retention compliance, or overall issue acceptance. Those limits and the independent staging/backup/key checks remain visible in issue #24 and the overall acceptance record.
