# Issue 23 local implementation checkpoint

Source: `2d9c65b`, whitespace correction `02cc379`. This records a local component increment, not whole-task acceptance. Issue 20 provider proof, applicable financial retention and hosted backup/restore durability remain outstanding.

Implemented owner-bound/versioned deletion previews and confirm/cancel through the existing Telegram ingress, application jobs, callback tokens and reply outbox. Confirmed deletion fences operational work, exports a minimal independent replay artifact and composes context-owned purge adapters. Text copies follow the approved 30-day rule; plans remain until deletion. Trial-used and active paid-access return facts remain separate from erased operational data. No financial or marker retention duration was invented.

The local `pin-retention run|replay` command and [runbook](../runbooks/account_deletion_local.md) provide maintenance and fail-closed replay. The ledger serializes writers within and across processes. Native tests model both a snapshot predating deletion and an already-recorded deletion followed by a legitimate return; they are not an actual hosted backup restore.

Latest focused packet: `C:/Users/roman/AppData/Local/Temp/pin-issue23-retention-mypy01-20261015`.

- Native PostgreSQL: 5 passed, zero failures/errors/skips, JUnit 18.833 seconds. Includes command/callback/cancel/confirm, local fake delivery transport, stale-delivery suppression, purge/export and retained Billing facts.
- Ledger/architecture: 10 passed. Mypy: 218 source files passed. Root independently checked mypy and architecture; full source/tests/landing Ruff passed after the final mechanical import correction.
- A new native fixture was required after replacing result rowcounts with `DELETE ... RETURNING`; the passing proof covers that changed SQL behavior.
- Cleanup log verifies the exact approved cluster, zero connections, only `pin_issue23_retention_20261015_mypy01` dropped and all other DB names unchanged. The earlier c91f2a fixture also has its own cleanup receipt. Failed runs remain in their packets.

Root started a new frozen full qualification of `02cc379`; it is pending. A focused pass does not substitute for changed-seam integration, actual Telegram/provider evidence, host recovery or whole-MVP acceptance. No issue closure is recorded here.
