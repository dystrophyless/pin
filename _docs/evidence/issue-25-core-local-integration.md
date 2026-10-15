# Issue 25 core local integration evidence

## Result

The native PostgreSQL test [`test_core_lifecycle_integration_postgres.py`](../../tests/test_core_lifecycle_integration_postgres.py) passed both `en` and `ru` journeys. It carried one saved PlanItem and its Occurrence through persisted private Telegram updates, application jobs, outbox consumers, and the actual reminder/accountability workers. The tested source SHA-256 was `6af872c36d510257e29566408ae0124b3009b61be0bdbc8f26187711bdee16f1`.

The final raw pytest log is at `C:\Users\roman\AppData\Local\Temp\pin-issue25-core-b61f-20261015\pytest-core-lifecycle-final.log`; its JUnit report with per-case UUID and fake-attempt properties is `C:\Users\roman\AppData\Local\Temp\pin-issue25-core-b61f-20261015\pytest-core-lifecycle-final.xml`. Fixture creation and cleanup receipts are in `C:\Users\roman\AppData\Local\Temp\pin-issue25-core-b61f-20261015\fixture-manifest.json` and `cleanup-proof.json`. Final run result: 2 passed in 10.31 seconds, zero skips.

The successful run used `pin_issue25_core_20261015_71c4` on `127.0.0.1:51017`, user `pin_test`, PostgreSQL 18.6, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. The fixture was created only after exact cluster identity and database-name absence checks. All six manifest-created fixture databases from this task were later dropped after verifying zero active connections; every non-manifest database name remained unchanged. The final test database is now absent.

## Journey and persisted assertions

Each language case starts with a fresh private Telegram identity and completes onboarding through persisted updates and application jobs. It checks selected language, home zone, reminder preferences, and completed onboarding state without seeding a business plan, occurrence, reminder, or accountability cycle.

The deterministic local interpreter transport returns one appointment proposal with missing duration. The real capture path asks the localized clarification, binds the duration choice to the owner and draft, produces a preview, and waits for a separate confirmation. Before confirmation there are zero plans and occurrences. After confirmation there is exactly one PlanItem and one linked appointment Occurrence, duration 30 minutes, and two reminder rules. Replaying the consumed confirmation token leaves those counts and rules unchanged and does not call the interpreter again.

The real Planning-to-Notifications consumer creates two intents for that occurrence at its configured calendar-day and elapsed-hour offsets. The delivery worker sends exactly two reminders through the local fake Telegram transport. At the occurrence end, the Accountability composition consumes the persisted Planning event and opens a cycle; the initial check delivery is recorded as confirmed. After no answer, exactly one nudge is scheduled and fake-confirmed. Its owner-bound callback produces a Done preview, and the separate mutation confirmation completes the same occurrence. After advancing the injected clock 24 hours beyond the nudge and running both schedulers/workers again, no additional reply is sent. Final state has one completed occurrence, the exhausted one-nudge cycle with exactly its initial and nudge intents, no scheduled/sending accountability intent, no scheduled reminder intent, and no pending or leased delivery job.

JUnit records the exact business rows and fake sends:

| Language | PlanItem | Occurrence | Reminder attempts | Reply attempts | Final assertions |
|---|---|---|---|---|---|
| EN | `76855149-0e02-481a-8ae4-ae95ed40fdd5` | `dda953da-8fc6-4d09-94e4-5f81b6eb754e` | `fake-reminder-001`, `fake-reminder-002` | `fake-reply-001` through `fake-reply-014` | completed; 0 outstanding delivery jobs; 0 replies after future sweep |
| RU | `68f78b1b-5386-41ac-abbf-c1f5cf88553a` | `304d41e3-18d6-4c87-8837-dcae05953c4f` | `fake-reminder-001`, `fake-reminder-002` | `fake-reply-001` through `fake-reply-014` | completed; 0 outstanding delivery jobs; 0 replies after future sweep |

The fake transports make no Telegram call. The injected interpreter makes no OpenRouter or other provider call; no provider spend occurred. The actual application, PostgreSQL persistence, workers, event consumers, and schedulers ran locally.

## SPEC-001 coverage boundary

This integration evidence covers selected portions of AC01, AC02, AC04, AC05, AC07, AC08, and AC12: onboarding preference persistence; one text-capture clarification/preview/confirmation; one confirmed appointment; two configured reminder offsets; appointment-end check-in; one unanswered-check nudge; Done on the same occurrence; and duplicate/stale confirmation protection.

It does not complete those ACs. It does not cover AC02's general duplicate-input contract; AC04's absent-time, untimed, or explicit-reminder override branches; AC05's approved Friday fixture, quiet-hours conflicts, or live Telegram delivery; AC07's deadline and untimed review cases; AC08's Reschedule/Cancel or unresolved-silence evidence; or AC12's send race and unknown-send outcomes. It does not cover AC03 voice, AC06 recurrence, AC09 agenda, AC10 live Telegram rendering/typing indicators, AC11 home-zone change/DST, or AC13 support. No live Telegram, provider, client-render, or hosted evidence is created here. TR-01 remains separate. This is not a full-MVP, hosted durability, release, or live-provider claim.

## Reproduction

Create a fresh, absent `pin_issue25_core_` database on the same exact local PG18.6 cluster after checking cluster identity and quoting the database identifier. Set `PIN_TEST_CORE_LIFECYCLE_DATABASE_URL` to that new database URL; the test verifies host, port, user, database prefix, data directory, and server version, then migrates to Alembic head. Do not reuse the cleaned database name from the evidence run.

Run `uv run --offline pytest -q tests/test_core_lifecycle_integration_postgres.py -vv -o junit_family=legacy --junitxml=<packet>/pytest-core-lifecycle.xml`. The final executed command and DB URL are retained in `command-final.txt`. Static checks passed: `uv run --offline ruff check tests/test_core_lifecycle_integration_postgres.py` and `uv run --offline mypy --no-incremental tests/test_core_lifecycle_integration_postgres.py`. After proof, verify no active connections and drop only the newly created manifest-owned fixture.

## Fixed-reference quiet-hours regression proof

The frozen qualification failure remains preserved and unchanged in `C:\Users\roman\AppData\Local\Temp\pin-final-frozen-local-qual-12aa6b5-20261015`: 547 passed and two failures (EN/RU) at the strict final `outstanding_jobs == []` assertion. Its read-only job diagnostic and event correlations show one pending `notifications.deliver_recovery_summary` job per owner after an applied `billing.access_period.activated` event consumed by `telegram.access_resume`.

The source confirms the full path. The access-resume consumer queues a recovery-summary job for a still-valid access period (a trial in this fixture). The real `deliver_recovery_summary_job` checks the owner's persisted `home_zone`, `quiet_start`, and `quiet_end`; when the current instant is quiet it defers the job with `fail_job(..., retry_after=...)`. At the frozen qualification's 22:43 Almaty run time, these legitimate recovery jobs remained pending past the core journey's final assertion. They were not reminder or accountability leftovers. The test assertion remains strict and unchanged.

The core test now uses a fixed reference instant, `2026-10-13T09:00:00+00:00` (14:00 in `Asia/Almaty`). The appointment remains three local calendar days later, Friday 16 October. The JUnit report records both the reference instant and zone. No product behavior or quiet-hours policy changed.

The corrected native PostgreSQL proof passed both language cases together: **2 passed, 0 skipped, 11.12 seconds**. Source SHA-256: `8b6976222aaaca3d45b577abab9c9fafa930e2ce9f93266219a1161a60a3d608`; repository HEAD during the run: `12aa6b5f9b3e69373780364ba0341954bbfec74e`. Raw command, log, JUnit, baseline, manifest, and cleanup receipt are at `C:\Users\roman\AppData\Local\Temp\pin-issue25-core-fixedclock-20261015-53bab5e9`.

The test used fresh database `pin_issue25_core_fixedclock_20261015_53bab5e9` on the assigned `127.0.0.1:51017` PostgreSQL 18.6 cluster and was migrated to Alembic head by the test fixture. Cleanup verified the exact cluster and fixture ownership, zero active connections, and that the only catalog difference from the recorded 174-name non-template baseline was this manifest-owned database. It dropped that database and verified the exact baseline restored; the three protected historical databases remain present. Ruff, focused mypy, and `git diff --check` passed.

This is targeted native evidence for the two core lifecycle language cases only. It does not replace the failed frozen full-suite packet or qualify the repository's whole local suite. No live Telegram, billing provider, or model call was made.
