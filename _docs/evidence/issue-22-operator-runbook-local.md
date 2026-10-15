# Issue 22 local operator runbook evidence

Date: 2026-10-15. Target repository: `D:\Program\python\pin`. Documentation was prepared against source revision `b41622c6a5cef2622450529edafe4a94b096a760`; the product source is unchanged from the qualified source revision `5464eec0f958c7bf40febf3dfe77ba0e5ed50fe5`.

## Artifacts

- [Local operations runbook](../runbooks/operations_local.md)
- Existing [account deletion and retention runbook](../runbooks/account_deletion_local.md)
- Existing [payment support and refund runbook](../runbooks/payments_local.md)

The new runbook maps the command's current JSON names and states to local investigation steps, preserves unknown external outcomes, and identifies the implemented scope limits. Source review covered `src/pin/entrypoints/operations.py`, `src/pin/platform/operations.py`, and the Billing, Conversation, Notifications, Speech, and Retention operational projections. Opaque identifiers and bounded ID lists are described as correlation values, not owner identities or payload access.

## Command evidence

Command: `uv run --frozen pin-operations --help`

Environment: Windows local repository checkout, 2026-10-15. Result: exit code `0`; argparse reported `usage: pin-operations [-h]`, described a one-shot redacted local readiness/worker snapshot, and exposed only `-h/--help`. This help invocation does not connect to PostgreSQL or call external services. The snapshot command itself was not run because the local shared database is in active use by another qualification; no live snapshot output is claimed.

## Existing qualification boundary

The runbook cites the frozen local qualification at source `5464eec` only as context; it does not claim that qualification tested alert delivery, live host failure injection, or restore readiness. The retained raw packet is `C:/Users/roman/AppData/Local/Temp/pin-combined-retention-qual-5464eec-20261015`. Its test result is not repeated here. `restore_status` is implemented as the literal `unavailable`; no off-host restore or alert integration proof exists in this documentation change.

No database was created, migrated, reset, or dropped. No worker was stopped, no queue or business state was changed, and no payment, provider, delivery, or retention command was executed. No tests were rerun for this documentation-only change.
