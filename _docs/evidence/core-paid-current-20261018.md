# Current core and paid lifecycle PostgreSQL verification — 2026-10-18

This is focused local integration evidence for the current runtime. It is not acceptance or closure of Issues 25 or 26 and does not establish hosted or provider behavior.

## Result

| Module | Result |
|---|---:|
| `tests/test_core_lifecycle_integration_postgres.py` | 2 passed, 0 failed, 0 skipped |
| `tests/test_recurring_lifecycle_integration_postgres.py` | 1 passed, 0 failed, 0 skipped |
| `tests/test_paid_lifecycle_integration_postgres.py` | 4 passed, 0 failed, 0 skipped |

The three modules ran on PostgreSQL 18.6 at the assigned local endpoint, using a separate fresh database per module and each module's strict endpoint, user, data-directory, driver, and database-prefix guards. Alembic reached `platform_recovery_gate_0001`. Before and after the runs, the complete database catalog matched the saved baseline: 174 non-template databases, with all three protected databases retained unchanged. Each owned fixture had zero active sessions and owner `pin_test` before its non-forced drop.

The checkout revision was `8c49d0ddf36b989b2bdc1bc4462224a61492e865`. Core and recurring ran before the paid-test-only correction described below; their source and test files were unchanged. The paid module ran against the corrected, dirty working-tree test file. Production source files were unchanged.

## Time-controlled quiet-hours case

The first paid-module run exposed a mismatch between the test's historical application clock and PostgreSQL's live job-claim clock. The worker consumed the trial activation, produced its owner-bound recovery-summary job, and correctly deferred it to the fixture's quiet-hours end. The test then repeatedly claimed that historical deadline because the live database clock considered it long past. A separate quiet-hours assertion also encoded the older retry-style `last_error="quiet_hours"` expectation, while the current policy deferral leaves `last_error` unset and stores the exact business quiet-end deadline.

The test now verifies the recovery-summary job's owner/episode identity, pending state, exact 90-minute quiet-end deadline, and absence of a transport send at the historical instant. It maps only that remaining interval onto PostgreSQL's live clock before asserting that the worker is idle. For the access-notice reply, it asserts the pending state, unset error, and exact quiet-end deadline first; when the injected business clock passes that deadline, it maps the deadline to the corresponding live-clock instant and verifies the same overdue offset before draining. The bounded drains and exact reply/reminder counts remain in place, along with the later expiry assertions.

The initial full paid run failed in the first historical drain. After correcting that path, validation exposed two further test-boundary details: the old retry-style expectation was stale, and the reply's live-clock projection had to be advanced when the injected business clock passed its quiet end. An isolated run of the trial-paid quota journey passed; the other intermediate full-module failures were cascades from the first case's leftover work, not independent failures. The final fresh-database full-module run passed all four tests.

## Commands and retained evidence

Each module used this command shape with its own environment variable and unique fresh database URL:

```powershell
$env:<module database variable> = 'postgresql+psycopg://pin_test@127.0.0.1:51017/<fresh guarded fixture>'
.\.venv\Scripts\python.exe -m pytest -q -vv -o junit_family=legacy --junitxml=<private packet>/<module>.xml tests/<module>.py
```

`ruff check tests/test_paid_lifecycle_integration_postgres.py`, scoped `mypy --no-incremental` on that test module, and `git diff --check` passed. Raw JUnit XML and logs for the original failure, intermediate checks, final module results, source hashes, cluster/catalog snapshots, fixture manifests, and cleanup receipts are retained in the private verification packet.

The test transports and synthetic payment events are local fakes. These runs made no Telegram, model, payment-provider, hosted, or other external calls. They cover only the scenarios in these three test modules and do not prove full issue matrices, live-client rendering, provider authentication, hosted deployment, or broad concurrency behavior.
