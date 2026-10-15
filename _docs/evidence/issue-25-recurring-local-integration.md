# Issue 25 recurring lifecycle and stale-dispatch local proof

Run on 2026-10-15 against the local Windows PostgreSQL 18.6 cluster. This is a focused local proof, not a claim that all Issue 25 acceptance criteria pass.

## Result

The composed recurrence journey passes on native PostgreSQL: one persisted weekly-every-two-weeks series is completed through its first two Telegram application routes, rescheduled through the real owner-confirmed “this and future” mutation route, reconciled by the real reminder-schedule consumer, and consumed by the real delivery worker. The journey proves that the confirmed dates become June 8, June 22, and July 6 while the two completed occurrences, all five occurrence IDs, series count, and history remain intact.

Replaying the consumed mutation confirmation returns the runtime’s stale-preview response and changes neither the series version nor the reminders sent. The old queued reminder job carries dispatch version 1 after reconciliation has advanced its persisted intent to version 2. The worker suppresses that old job before transport, records the skipped attempt and `notifications.delivery_attempt_outcome` event under version 1, then sends the eligible revised reminder exactly once under version 2 and publishes version 2. A second worker drain sends nothing further.

The stale-dispatch defect found during this journey was fixed in `src/pin/contexts/notifications/infrastructure/delivery_worker.py`: attempts and typed outcome events now retain the queued dispatch version when suppressing stale work; attempt numbering is scoped to that version. This prevents a skipped version-1 job from leaving a terminal version-2 attempt that blocks the revised reminder. Missing-destination suppression also records the queued version. Existing current-version outcome handling and started/unknown-send recovery remain fenced as before.

## Journey and boundaries

- Fixed reference clock: `2026-04-27T09:00:00Z`; recurrence is created for May 4 at 14:00 in `Asia/Almaty`, every two weeks, five occurrences.
- The test posts persisted private Telegram-shaped capture updates and routes `/done` and `/reschedule` through the application worker. The interpretation result and reminder transport are injected local fakes; they are not real model or Telegram calls.
- The synthetic mutation confirmation is owner-bound and consumed once. Replaying it returns the observed stale-preview response. The actual pending schedule consumer runs before reminder intent assertions.
- Raw JUnit properties record the reference, mutation (`2026-05-31T09:00:00Z`), and revised reminder dispatch (`2026-06-07T09:00:00Z`) instants.
- No actual Telegram, model, payment/Stars provider, or external API call was made. No Docker, deployment, or spending was involved.

The proof covers this recurrence/mutation/reminder composition and the versioned outcome regression only. It does not establish the full Issue 25 matrix, hosted behavior, voice or timezone-handoff journeys, broad concurrency/restart coverage, or any live-provider behavior.

## Verification

Final composed run: `1 passed, 0 skipped`.

```powershell
$env:PIN_TEST_RECURRING_LIFECYCLE_DATABASE_URL = 'postgresql+psycopg://pin_test@127.0.0.1:51017/pin_issue25_recurring_20261015_e477a4b_r10'
.\.venv\Scripts\python.exe -m pytest -q -vv -o junit_family=legacy --junitxml=<packet>\pytest-recurring.xml tests/test_recurring_lifecycle_integration_postgres.py
```

Existing notification-delivery PostgreSQL module: `30 passed, 0 skipped`, including the stale-dispatch component assertion that the suppressed attempt remains version 1 while the intent is version 2.

```powershell
$env:PIN_TEST_DELIVERY_DATABASE_URL = 'postgresql+psycopg://pin_test@127.0.0.1:51017/pin_issue12_delivery_20261015_26fixa'
.\.venv\Scripts\python.exe -m pytest -q -vv -o junit_family=legacy --junitxml=<packet>\pytest-delivery.xml tests/test_notification_delivery_postgres.py
```

`ruff check src/pin/contexts/notifications/infrastructure/delivery_worker.py tests/test_recurring_lifecycle_integration_postgres.py tests/test_notification_delivery_postgres.py` passed. `.venv/Scripts/mypy.exe src/pin tests/test_recurring_lifecycle_integration_postgres.py` passed (220 files). Running mypy over the existing `test_notification_delivery_postgres.py` module also reports 26 strict annotation/parametrized-kwargs diagnostics in that module; the added stale-dispatch assertions produced no diagnostic.

The checkout HEAD during the final proof was `fa37ea27414143edf72734d23645a767c76b06ba`; these three implementation/test files were working-tree changes at that boundary. SHA-256:

| File | SHA-256 |
| --- | --- |
| `src/pin/contexts/notifications/infrastructure/delivery_worker.py` | `693D1881A8ECEF94B2767F0BAE8577325E584632AB7B10039BC3360CCA6591AD` |
| `tests/test_recurring_lifecycle_integration_postgres.py` | `80FFD0D9F7ED0AD8A3BEE80668B65EBD34E94A150CCD5061F3F195DCF00D08E2` |
| `tests/test_notification_delivery_postgres.py` | `A5C7B876422F6311A450273375B493AD27BF21887C377E9258B229C0CC9F8257` |

## PostgreSQL fixture and raw packets

Both final runs used fresh manifest-owned databases on the exact endpoint `127.0.0.1:51017`, role `pin_test`, PostgreSQL 18.6 (`180006`), data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. Before each creation the non-template catalog contained exactly 174 names and the fixture name was absent. The protected model-adapter and two Issue 20 sandbox databases were present. Before each drop, the catalog was exactly that 174-name baseline plus the one manifest fixture and the fixture had zero active connections. After each drop, the exact 174-name baseline was restored and the fixture was absent.

- Final composition packet: `C:/Users/roman/AppData/Local/Temp/pin-issue25-recurring-20261015_e477a4b_r10/` (`pytest-recurring.xml`, `pytest-recurring.log`, command, fixture manifest, pass metadata, `pass-delivery-correlation.txt`, and pre-drop/cleanup audits).
- Existing delivery regression packet: `C:/Users/roman/AppData/Local/Temp/pin-issue25-deliveryreg-20261015_26fixa/` (`pytest-delivery.xml`, `pytest-delivery.log`, command, fixture manifest, and pre-drop/cleanup audits).
- Preserved pre-fix defect packet: `C:/Users/roman/AppData/Local/Temp/pin-issue25-recurring-20261015_e477a4b_r8/` (`pytest-recurring.xml`, `pytest-recurring.log`, failure metadata, and `revised-reminder-failure-diagnostic.txt`). It records the old version-1 job, version-2 intent, and the poisoned skipped version-2 outcome before the revised reminder was lost. The r8 database was dropped after its raw evidence was captured. Earlier failed local assertion attempts r2–r7 and their cleanup receipts remain preserved in their separate temp packets.

No commits or repository-wide cleanup were performed. The unrelated untracked `output/` directory was preserved.
