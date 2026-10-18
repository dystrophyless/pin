# Composed core recovery/check-in proof — 2026-10-18

The core lifecycle integration test now retains the ordinary EN/RU path and adds a separate recovery mode for each language. This is test-only work against real PostgreSQL and application/Planning/Notifications/Accountability consumers, with deterministic interpretation and fake external transport. It does not establish a real Telegram outage or model quality.

## Outcomes

- Ordinary EN/RU: persisted onboarding, missing-duration choice, zero plan writes before explicit confirmation, one appointment, both advance reminder sends, initial appointment-end check, and the bounded follow-up/action lifecycle remain covered.
- Recovery EN/RU: no delivery worker runs across the advance deadlines and appointment end. At recovery five hours after end, the existing open cycle has no initial confirmation, intent or delivery attempt. One idempotent recovery-summary job sends a summary without confirming that cycle. A separate late initial check is then delivered; the two expired advance reminders are skipped as `event_already_started` without transport sends.
- The single nudge timer starts at the confirmed late-check instant. Its raw one-hour deadline falls inside the selected quiet window; the persisted intent moves to quiet-end, with no early send. After that one nudge the cycle is exhausted. Replaying the summary episode and later sweeps create no second summary or check-in campaign. The existing owner-bound Done confirmation still finishes the plan and leaves no outstanding delivery work.

The controlled application clock is separate from PostgreSQL's job-claim wall clock. After asserting the exact quiet-end intent and no early send, the fixture moves only the exact owner-bound pending nudge job's `available_at` to the database clock so the real worker can claim it. It does not change production clock logic, intent due time, external behavior, or another job.

## Verification and limits

`tests/test_core_lifecycle_integration_postgres.py` collected and executed four cases: ordinary EN/RU and recovery EN/RU. Raw JUnit reports **4 passed, 0 failures, 0 errors, 0 skips**, suite time 17.010 seconds. Root inspected the raw result, final test diff and matching test-source digest. Ruff, compilation, collection and diff checks passed. No production source was changed for this proof.

The initial attempts exposed fixture mistakes: a cycle already exists after the Planning event, the delivery-attempt relation uses an intent join, and the job claim uses the database clock. Their failed outputs remain preserved separately from the final passing result. The final disposable fixture was removed by an ordinary guarded drop after ownership/session checks; the exact 174-database baseline and the three protected databases were preserved. No forced drop or session termination was used.

This adds composed evidence for the approved late-initial-check decision while preserving ordinary reminder-send coverage. It does not close Issue 25, prove current live advice behavior, resolve the later language-only reminder eligibility bug, or waive legal/provider/final acceptance conditions.
