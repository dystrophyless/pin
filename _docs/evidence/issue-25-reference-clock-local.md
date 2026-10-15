# Issue 25 reference-clock local verification

This note records scoped local evidence for the receipt-anchored reference instant change. It is not a release gate or full issue acceptance.

## Boundary

- Repository: `D:\Program\python\pin`
- Git HEAD: `8cf35189c4b41b9dd5b46247af6065e8335a4eda`; the implementation and focused test changes were uncommitted in the worktree during verification.
- Verification date: 2026-10-16 (local Windows workstation, Python 3.12.12).
- Source and test SHA-256 manifest: `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\source-hashes.json`.

## Change verified

Conversation context now carries an aware UTC `reference_instant` and validates that `reference_date` is the date of that instant in the profile timezone. The bounded conversation and smoke prompts serialize the instant and explain that elapsed requests such as “in two hours” / “через два часа” are computed by adding elapsed duration to the UTC receipt instant, then converting to the event timezone. Calendar-day and pre-event elapsed reminders retain their existing meanings.

Text and prepared-voice processing use the original inbox receipt instant when building the context, including delayed/retried processing. Polling supplies the wall-clock instant observed when an updates batch returns; webhook supplies the wall-clock instant observed at handler entry. Those explicit receipt instants are persisted with the inbox row, while the separate monotonic clock remains in use for payment deadline calculations. A duplicate inbox update does not replace the first stored receipt time. These are adapter-observation timestamps, not Telegram server event-creation times or packet-arrival timestamps.

## Checks

All commands used the locked offline environment; no dependencies or model assets were downloaded.

| Command | Result | Raw evidence |
| --- | --- | --- |
| `uv run --locked --offline pytest tests/test_model_adapter.py tests/test_capture.py tests/test_advice_bounds.py tests/test_telegram_support.py tests/test_application_capture_runtime.py tests/test_telegram_ingress.py tests/test_webhook_health.py` | 81 passed | `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\final-current-unit.log` |
| `uv run --locked --offline ruff check src tests` | Passed | `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\final-current-ruff.log` |
| `uv run --locked --offline mypy src` | Passed; 219 source files | `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\final-current-mypy.log` |
| `uv run --locked --offline pytest tests/test_architecture.py` | 2 passed | `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\final-current-architecture.log` |
| `git diff --check` | Exit 0; Git emitted only line-ending normalization warnings | `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\final-current-diffcheck.log` |
| `uv run --locked --offline pytest tests/test_application_capture_runtime_postgres.py::test_completed_profile_text_runs_through_fenced_capture_runtime tests/test_prepared_voice_capture_postgres.py::test_capture_and_speech_completion_commit_atomically_after_replay --junitxml=...` | 2 passed, 0 failures/errors/skips | `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\focused-native-attempt-4.log` and `focused-native-attempt-4.xml` |

The focused offline checks also cover aware-instant validation, profile-zone date consistency, English/Russian prompt context, same-profile-day contexts that differ only by receipt instant, webhook and polling timestamp propagation, duplicate-receipt stability, delayed application processing, prepared-voice continuation, and the unchanged processing-time billing fence.

## Native database and cleanup

The final exact-source native run used PostgreSQL 18.6 (`server_version_num=180006`) at `127.0.0.1:51017`, role `pin_test`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. Before fixture creation, the non-template catalog matched the recorded 174-database baseline and both assigned names were absent. The run used only these fresh manifest-owned databases:

- `pin_issue14_runtime_refclock_20261016_9c8a1e_r4`
- `pin_issue18_prepared_refclock_20261016_9c8a1e_r4`

After the tests, cleanup revalidated cluster identity and fixture ownership, confirmed no active connections, dropped only those two names, and verified both absent. The final catalog again contained exactly the original 174 non-template databases; the catalog matched the pre-create baseline and the three protected databases remained present. The independent post-cleanup audit and cleanup receipt both report `PASS`:

- `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\cleanup-result-attempt-4.json`
- `C:\Users\roman\AppData\Local\Temp\pin-issue25-reference-clock-local-20261016-9c8a1e\post-cleanup-independent-audit-attempt-4.json`

Earlier attempts and their raw failures are retained in the same packet; each created fixture set was cleaned and catalog-audited. The final result is attempt 4, not a combination of partial attempts.

Root verification: independently checked raw attempt-4 JUnit (2tests,0failures/errors/skips,7.609s), the81-test console result, and all19source/test hashes against the current worktree. A separate focused invocation of the five receipt-context/validation/polling/webhook cases passed5/5 in9.55s. Root's read-only live PostgreSQL query matched all174pre-create catalog records, including names, owners and connection flags, with all three protected databases present and both attempt-4 fixtures absent. The first root catalog-comparison helper used the wrong JSON key (`datname` instead of `name`) and failed before comparison; the corrected read-only comparison passed. These focused checks do not constitute a full-suite result.

## Limits

The focused native tests use a fake interpretation transport to inspect the actual outbound request context. They do not establish that a live model interprets relative-hour language correctly. No Telegram, provider, payment, or other external calls were made. No full repository pytest suite was run. No schema or migration files changed, and this evidence does not claim a migration release qualification. Polling timestamps represent when the returned batch was observed locally; webhook timestamps represent handler entry. Neither proves upstream event creation time or precise network arrival time.
