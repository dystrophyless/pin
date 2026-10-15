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

This first slice does not call Telegram, a payment provider, a model provider, Docker, or a hosted environment, and it incurs no spend. Receipt inputs are explicitly synthetic trusted transport events: it does not prove Telegram/provider authentication or external invoice/renewal/refund transport. The subsequent cancellation/refund slice below adds local coverage of those rights. Concurrent receipt/refund races, account deletion interleavings and other issue #26 seams remain outside these two composed journeys. Owner direction removes actual Stars testing as a required gate. No live-provider proof is claimed.

## Cancellation and refund rights slice

A second, separate fresh-database journey was added to the same test module on 2026-10-15. It passed **1 selected test, 0 skipped** in 8.13 seconds (`1 deselected` is the earlier trial/paid journey). The source SHA-256 for this run was `0897ddfcc4aea09231ee49071ad4d675b78309a59745bec11e0bc7d408a61957`, on checkout revision `70474a0167b46842ec8ba860d7904f26765aa448` (the run manifest records this checkout).

The scenario saves two confirmed Plans and reminder intents, creates an initial synthetic paid period and a synthetic next period through the actual payment store, and routes `/cancel` through persisted private Telegram ingress and the application worker. The actual delivery worker claims the cancellation request and records a confirmed outcome through the local fake transport. The real `/subscription` reply reports auto-renewal disabled while the second paid period remains active. A reminder is confirmed during that paid-through coverage.

The test then consumes one AI turn in the active second period, requests a full refund of the earlier receipt through `begin_refund`, and injects the confirmed outcome through `finish_refund`. The earlier paid-period row is removed; the unrelated current period remains active. Replaying the refunded receipt is idempotent: it does not recreate the removed period, add a receipt, change the current period ID, or restore the consumed turn. The actual `/subscription` reply still reports 999 turns and disabled renewal. Both original plans and reminder intents remain, and a second reminder is confirmed under the surviving paid period.

The final raw pytest log and JUnit report are `C:\Users\roman\AppData\Local\Temp\pin-issue26-cancel-refund-20261015-3ac72fc1\pytest-cancel-refund-r4.log` and `pytest-cancel-refund-r4.xml`; the full command, manifest, original database-name baseline, three preserved harness attempts, and cleanup receipts are in that packet. The successful fixture `pin_issue26_paid_20261015_cancel_refund_3ac72fc1_r4` was created only after exact cluster identity and absence checks. Cleanup verified zero active connections, dropped only that manifest-owned database, and confirmed the catalog exactly matched its saved baseline. Earlier attempts were preserved and separately cleaned with the same checks.

This second slice exercises local trusted synthetic receipts and a synthetic confirmed refund outcome. The cancellation transport and Telegram reply/reminder transports are fakes; no live Telegram or refund API call occurred. It does not cover concurrent receipt/refund races, unknown external cancellation/refund outcomes, or the remaining issue #26 lifecycle seams. It is additional local evidence, not full issue acceptance.


The shared fake delivery transport was then verified by running the **entire two-case file together** on another fresh database: **2 passed, 0 skipped** in 51.43 seconds. This combined run used source SHA-256 `0897ddfcc4aea09231ee49071ad4d675b78309a59745bec11e0bc7d408a61957` at checkout revision `35e15d6cbf52beddefdf39151b48213fb63f788b`. Its fixture `pin_issue26_paid_20261015_cancel_refund_combined_3ac72fc1` had the same verified PostgreSQL endpoint and data directory. The complete command, raw log, JUnit report, baseline, manifest, and cleanup receipt are in `C:\Users\roman\AppData\Local\Temp\pin-issue26-cancel-refund-20261015-3ac72fc1`; cleanup observed zero active connections, dropped only this fixture, and restored the exact 174-database non-template baseline. The run confirms both local journeys coexist through the modified shared transport.
