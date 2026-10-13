# Issue #10 — local recurrence acceptance

Date: 2026-10-14, Asia/Qyzylorda. Direct Orchestrator acceptance applies to the Windows local-development increment, not full MVP or hosted release qualification.

## Revision and environment

- Source: `35a5fbfc1a11b485be087ba2e27dd3e353bae697`, local and not pushed.
- Exact committed-source export: `C:/Users/roman/AppData/Local/Temp/pin-parent-recurrence-d92a/source`; the import-provenance check resolved `pin` inside that export.
- Windows, CPython 3.12.12, local PostgreSQL 18.6. All eleven PostgreSQL suite opt-ins used fresh disposable databases on the guarded owned cluster at port 51017. No credentials or local environment files were copied into the source export.
- Packet: `C:/Users/roman/AppData/Local/Temp/pin-parent-recurrence-d92a`, containing scripts, database manifest, and check logs. Source/public facade and metadata registration were committed before verification.

## Observed checks

`verify.py`, run with the repository virtual-environment interpreter against the export, passed:

- Full pytest: **209 passed, no skips, 56.38 seconds**.
- Ruff: all checks passed.
- Mypy: no issues in 100 source files.
- Architecture import boundaries: PASS.

Parent result chunk: `bdebbc`. Recurrence contributes eleven pure calendar checks and ten PostgreSQL cases; the full suite also exercises accepted Planning, agenda, Identity, Billing, ingress, work, presentation and model foundations without new provider calls.

`migrations.py` passed upgrade/head/current/check, downgrade/base, re-upgrade/head/check. The single head is `planning_recurrence_0001`, following `agenda_pages_0001`; both drift checks reported no new upgrade operations. Final result chunk: `0f2c14`.

## Contract coverage

- Daily, weekly and monthly positive-N rules; selected weekdays, day-of-month and last-day patterns; optional count/end and unbounded series.
- January 31 clamps in February and returns to March 31. DST gap shifts by its exact duration; a repeated hour uses the earlier instant once and discloses the choice.
- Bounded generation persists its cursor, survives a new session/restart and horizon extension, and serializes simultaneous generators without duplicate occurrence keys. Occurrences retain independent terminal states.
- The approved count-ten/four-completed fixture preserves history and the remaining six. The approved two-week fixture moves June 1/15/29 to June 8/22 and July 6, repeating from the newly selected date without restarting the remaining count.
- Single and future exceptions preserve stable occurrence identities and require exact versioned projection approval. Reminder fields participate in receipt identity; explicit replacement reminders, including an empty list, propagate through preview, persistence and subsequent generation.
- Owner, current preferences, access and proof checks precede atomic series/occurrence/reminder/receipt/outbox writes. Expired retained owners can stop generation through `CANCEL_PLAN`; generated occurrence states remain unchanged.
- Three paused-verifier PostgreSQL cases complete, cancel or advance the target occurrence version concurrently. Refreshed deterministic child locks reject the stale edit, preserve the external state/version, leave the proof unconsumed, and commit no exception, action receipt or additional outbox event. Parent inspected this write-boundary guard and rollback assertions directly.
- Consumers use the standard `pin.contexts.planning.api` facade. Generated rows are ordinary Planning occurrences; no business policy was moved into platform infrastructure.

## Cleanup and limits

All eleven new parent-owned databases were dropped and verified absent (`c82253`), and the stopped recurrence worker fixture was dropped with the same exact cluster guard (`a89784`). Historical provider-cost evidence was preserved.

This increment supplies durable typed recurrence ports and real local PostgreSQL proof. User capture/edit UI, production generation scheduling, reminder dispatch, coaching and full live conversation qualification belong to their dependent issues. The confirmation issuer in the recurrence tests is a persisted test producer, not a completed production capture feature. No new provider calls, payment calls, push, Docker, deployment, Linux/hosted qualification or full-MVP acceptance is claimed.

GitHub acceptance publication/readback follows this local evidence packet.
