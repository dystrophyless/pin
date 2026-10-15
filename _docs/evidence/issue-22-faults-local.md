# Issue 22 local operational fault snapshot evidence

Date: 2026-10-15. Qualification base: `main` at `3514807a943e06c96385f16e0298f3043cc7510a`. The worktree contains a focused PostgreSQL test and this evidence record; the product implementation is unchanged.

## Finding

The existing `src/pin/platform/operations.py` projection provides the local operator fields reviewed here: counts for application, speech, and delivery job lanes; oldest globally due pending-job age; capped dead and expired-lease job ID lists; and stopped, stale, or running worker heartbeats. It does not select a job payload or owner ID. No product-source defect was found in this review.

The new test `tests/test_operations_postgres.py::test_operational_fault_snapshot_reports_bounded_redacted_queue_and_worker_states` prepares synthetic rows through platform APIs and the heartbeat writer. It creates 26 dead jobs and 26 leased jobs across all three lanes, three due pending jobs, and an older-created but future-only pending job. Dead work uses `enqueue_job`, `claim_job`, and `fail_job`; lease timestamps are moved to the past only after all intended jobs have been claimed, so setup cannot reclaim a previous row. Assertions cover exact lane/status counts, due-age exclusion of the future-only row, both 25-ID caps and seeded-ID membership, heartbeat states, and redaction of synthetic owner/task/API-key-shaped/error sentinels from the platform and CLI projections. The platform read runs in a read-only transaction; persisted job and heartbeat rows are compared before and after projection.

## Native run

The focused command, run with the locked offline environment and a scrubbed credential environment, was:

`uv run --locked --offline --extra speech python -m pytest -vv --junitxml=C:\Users\roman\AppData\Local\Temp\pin-issue22-faults-3514807-20261015\pytest-attempt-2.xml tests/test_operations_postgres.py`

JUnit reports **3 tests, 0 failures, 0 errors, 0 skips**; pytest exited `0` in 16.967 seconds. It includes the existing worker-heartbeat/migration-roundtrip and retention-projection tests plus the new fault snapshot case. The module fixture upgraded to Alembic head and checked migration consistency; the roundtrip test downgraded to `billing_payments_0001`, upgraded to head, and checked again. Full command output and JUnit are in the packet.

The first invocation attempt is also retained: a PowerShell argument-construction error caused pytest to treat the intended XML path as a positional test path, exit `4`, and collect zero tests. It performed no test or migration. The corrected invocation produced the JUnit result above.

## Fixture identity and cleanup

Only the manifest database `pin_issue22_ops_faults_20261015_a474b9ff` was created. Preflight and pre-drop checks observed PostgreSQL `18.6` (`180006`) at `127.0.0.1:51017`, authenticated as `pin_test`, maintenance database `postgres`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. The non-template catalog was 174 before create, 175 with the fixture, and had zero active fixture connections before cleanup. The database owner was `pin_test`.

Cleanup dropped only that exact manifest name. A fresh independent catalog query verified the fixture name absent, all three protected databases present (`pin_model_adapter_20261013`, `pin_issue20_sandbox_20261014_25eb0b51`, and `pin_issue20_sandbox_20261014_33a6ed3b`), and the full non-template `{name, owner, allow_connections}` catalog exactly equal to its 174-row pre-create snapshot. Raw query output, exit codes, snapshots, and manifest are recorded in the packet.

## Static checks and provenance

- `uv run --locked --offline --extra speech python -m ruff check tests/test_operations_postgres.py` — exit `0`.
- `uv run --locked --offline --extra speech python -m mypy --no-incremental --follow-imports=silent tests/test_operations_postgres.py` — exit `0`.
- `uv run --locked --offline --extra speech python -m pytest --collect-only -q tests/test_operations_postgres.py` — exit `0`, 3 collected.
- `git diff --check` — exit `0` after the lease-setup ordering adjustment. Ruff, mypy, and collection were also rerun after the adjustment.

The reviewed implementation source SHA-256 is `E113C4BC59FC64BD5B194278C820DD5C978046D1F78CBEB175DD60C10C2C6B66`. The final test working-tree SHA-256 is `264CA8BF11A96943C6640E3DCB0DD17EAC78A0844E12E0D15A252938ACE66A68`; its base blob is `dd9fd8aaa9d33e57edfeca5582c538874b92dccc`. No product source was changed. The pre-existing `output/` and concurrently appearing `tests/test_recurring_lifecycle_integration_postgres.py` were left untouched.

The packet is `C:/Users/roman/AppData/Local/Temp/pin-issue22-faults-3514807-20261015`; see `source-provenance-final.json`, `fixture-manifest.json`, `pytest-attempt-2.xml`, command logs, and catalog receipts there.

## Scope limits

This is local synthetic diagnostic coverage only. It does not prove hosted alert delivery, host failure behavior, off-host restore, provider fault or reconciliation, actual Telegram send outcomes, or whole Issue 22 acceptance.

