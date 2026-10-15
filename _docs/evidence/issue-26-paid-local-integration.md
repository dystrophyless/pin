# Issue 26 paid-access local integration evidence

## Result

The focused native PostgreSQL journey passed on 2026-10-15: **1 passed, 0 skipped** in 113.66 seconds. It composes persisted private Telegram ingress, the actual `/subscription` application route, trial and paid usage stores, Planning's reminder-schedule consumer, the delivery worker, and the Stars payment store in one frozen-clock scenario. This is a local slice of issue #26, not a full issue acceptance or release claim.

The executed test is [`test_paid_lifecycle_integration_postgres.py`](../../tests/test_paid_lifecycle_integration_postgres.py). Its SHA-256 was `6d9b9d542905bc96d416735883f85ae1783caab51a3b3cb072c45b4c41848630`; the checkout revision was `4fd23d422cf402c60ded67862c6394eda4b9e8af`. The test was an untracked local addition, and the separate existing `output/` directory was preserved.

The raw final pytest log and JUnit report are `C:\Users\roman\AppData\Local\Temp\pin-issue26-paid-20261015-7f30a6e1\pytest-paid-lifecycle-r3.log` and `pytest-paid-lifecycle-r3.xml`. The complete final command is in `command-final.txt`. The fixture baseline, ownership manifest, two preserved harness-failure runs, and cleanup receipts are in that same packet. Only the third fresh-fixture run passed; the first two are retained as failed attempts and are not counted as proof.

## Journey and persisted assertions

The test compares the English landing offer copy against `PlanPolicy` and Stars constants, including the trial duration and allowances, paid duration, paid allowances, Stars price, and the still-unverified offer disclaimer. It sends a persisted private `/subscription` update through `process_application_job` and checks the emitted reply against the stored access snapshot.

The owner starts a seven-day trial and confirms four tasks through the real `create_confirmed_plan_batch` path using a one-use local confirmation verifier. Planning's consumer creates the persisted reminder intents. The test reserves and settles all 250 trial AI turns through `SqlAlchemyAccessUsageStore`; `/subscription` then reports zero remaining turns while an actual worker-delivered reminder is confirmed.

The billing store accepts a checkout-prechecked synthetic recurring receipt, then its exact replay. The persisted state has one receipt and one paid period, with a fresh 1,000-turn allowance and the provider-defined period end. After all 1,000 paid AI turns are reserved and settled, the actual `/subscription` route reports zero remaining and says reminders continue independently. A reminder is confirmed while this paid quota is exhausted.

A second synthetic trusted receipt exercises one renewal at the exact boundary of the first period. Replaying it leaves exactly two receipts and two paid periods. A reminder after the original period end is confirmed against the renewed period; a later reminder after the renewed period end is skipped with `access_expired`. The fake delivery transport records local attempt IDs only. Each delivery is driven through a bounded loop of the actual delivery worker until idle.

## Fixture, verification, and cleanup

The successful run used the manifest-owned database `pin_issue26_paid_20261015_7f30a6e1_r2_r3` on `127.0.0.1:51017`, user `pin_test`, PostgreSQL 18.6, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. Before creation, the fixture script verified the exact endpoint and captured the database-name baseline; the chosen name was absent. After the run, cleanup reverified cluster identity and the exact baseline-plus-fixture catalog, observed zero active connections, dropped only that manifest-owned database, and verified the complete database-name set returned to baseline. The other two manifest-owned failed-attempt databases were separately cleaned with the same zero-connection and catalog checks. The protected historical databases were not modified.

Final focused checks passed:

- `uv run --offline pytest -q tests/test_paid_lifecycle_integration_postgres.py -vv -o junit_family=legacy --junitxml=<packet>/pytest-paid-lifecycle-r3.xml` with `PIN_TEST_ISSUE26_PAID_DATABASE_URL` pointed at the manifest fixture: 1 passed, zero skipped.
- `uv run --offline ruff check tests/test_paid_lifecycle_integration_postgres.py`
- `uv run --offline mypy --no-incremental tests/test_paid_lifecycle_integration_postgres.py`

The test does not call Telegram, a payment provider, a model provider, Docker, or a hosted environment, and it incurs no spend. The receipt inputs are explicitly synthetic trusted transport events: the test does not prove Telegram/provider authentication, a live Stars charge, or the external invoice/renewal/refund transport. It also does not cover payment/refund races, account deletion interleavings, cancellation outcomes, or the other concurrency and lifecycle seams in issue #26. Owner direction removes actual Stars testing as a required gate. No live-provider proof is claimed.
