# Issue #16 Accountability implementation progress

Issue #16 remains open. Requirements are the published task contract and SPEC-001 §11. No final acceptance is claimed.

Checkpoint `c9a2857` adds persisted per-occurrence checks and grouped daily reviews, bounded follow-up intents, Planning/outcome consumers, private owner-bound item actions entering ordinary mutation previews, and native delivery/application composition. New migration `accountability_cycles_0001` follows `telegram_mutation_runtime_0001`.

The completed executor reported the following native PostgreSQL 18.6 results on 2026-10-14:

- Runtime: **19 passed** on fresh `pin_issue16_runtime_20261014s`; initial confirmation and retries, failed/unknown outcomes, appointment due boundaries, quiet deferral, shared blocked destinations, preference races, scheduler/send lock ordering, terminal mutation after attempt-start, callbacks, expiry, renewal, rescheduling and recurring occurrence events.
- Core Accountability: **4 passed** on owned `pin_issue16_accountability_20261014`.
- Private application path: **1 passed** on fresh `pin_issue16_app_20261014f`; persisted callback/application jobs, foreign actor and replay, ordinary preview without writes/AI allowance, exact-once confirmed completion event, and actual delivery-loop consumer closure.
- Unit/quiet policy: **15 passed**. Scoped mypy and Ruff passed. Failed/interfering earlier fixtures were preserved rather than reset.

Direct Orchestrator checks at the integrated checkpoint: whole-source mypy passed on **175 files**, Ruff passed. Architecture failed because two Accountability imports used `notifications.quiet_hours_api` instead of the published `.api`; fixing the public export/import cycle is pending. Further concrete audits identified outcome-consumer lock-order inversion and prior-started-attempt recovery being checked after access denial. Corrections and native regression evidence are in progress.

Follow-up source checkpoints `e5aa5e2` and `46b431b` resolve those defects: quiet policy is exposed through the published Notifications API; a lazy dispatch wrapper retains an explicit typed signature; outcome application locks the subject before the intent; prior started attempts reconcile to unknown before eligibility checks for a new external send. Orchestrator full-source mypy now passes on **174 files**, as do full Ruff and architecture. Two native defect regressions passed on fresh `pin_issue16_runtime_20261014u`, and both lock-order races passed on `...14v`. Executor's full runtime suite then passed **21 cases** on fresh `...14x`, with test-only owner-scoped retirement after assertions to prevent a previous synthetic open cycle creating a nudge in later scenarios. The earlier `...14w` failure fixture is preserved. This mutable-source run preceded completion of the final outer API import adjustment; exact-source qualification remains pending.

These checks used the mutable development checkout and injected transport/clock. Some recurring producer fixtures construct typed persisted producer-shaped events rather than invoke Planning commands; actual private confirmation exercises a real completion producer. Final frozen-source whole-suite qualification, migration lifecycle/drift checks and complete acceptance audit are pending in packet `pin-parent-accountability-support-20261014-1617a`.

An accidental second run on already populated `...14x` produced **10 failures / 11 passes** from cross-run shared-queue interference. That database and evidence are preserved; the fresh first-run result is not a repeatability claim. The final qualification uses a new isolated fixture for this test file and retains all raw results.

The cluster is `127.0.0.1:51017`, user `pin_test`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. Historical model receipts and prior fixtures remain untouched. No live Telegram/model requests, Docker, deployment or real-host qualification were performed. Real grouped-review evidence remains an explicit core-gate requirement.

## Frozen-source diagnostic run

Packet `C:/Users/roman/AppData/Local/Temp/pin-parent-accountability-support-20261014-1617a` froze source `1c1b87484001217bb505cf3a8a73e94e9f74907a`, tree `6b5d865c1efdeaffd61984bd88c6c7c06103909b`. All 28 fresh guarded fixtures were created. The initial creation preflight failed before any database write because it counted `.venv` files; the packet guard was corrected to compare the exact committed file inventory, and that failure receipt is preserved.

Migration upgrade/current passed, but the first drift check failed: ORM described the outcome owner/event uniqueness as an index while the migration created a unique constraint. Source correction `c8ef0bd` aligns ORM metadata with the migration; the frozen source remains untouched. The remaining lifecycle steps were not claimed as passed.

Diagnostic full suite: **448 passed, 4 failed, zero skips**, 197.02 seconds. Failures were one generic reply deletion-boundary fixture and three real local Whisper/voice-continuation cases. Native Accountability and bilingual support cases passed within this complete run. The reply fixture mixed a fixed 07:00 clock with later PostgreSQL outbox creation time; speech records show decoding succeeded and recognition failed before model continuation. Fixes are assigned to separate Luna High executors. Full Ruff, no-incremental mypy174 and architecture passed; overall qualification is **FAIL**. Raw logs/JUnit and all fixture records are retained while diagnosis continues.

Packet `1617b` is prepared with new fixture names and corrected packet safety guards. It has not frozen a source or created databases. Final qualification, offline speech and manifest-bound cleanup remain required before issue acceptance.
