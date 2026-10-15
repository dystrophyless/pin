# Issue 22 context fault projections: local evidence

Date: 2026-10-16 (Asia/Qyzylorda). Checkout revision during the native run: `544358aee9efe6cba9b56b91c3357f6484c7c2f1`. Test source SHA-256: `3AE64A280338E6C0F12AD0D31682CAF08AE606BBA8D04038ED6F80CA16748B3C`. Product source was unchanged.

## Result

The complete four-case `tests/test_operations_postgres.py` module passed on Windows / CPython 3.12.12 with the locked offline environment: **4 passed, 0 failures, 0 errors, 0 skips** in 19.159 seconds. Raw output and JUnit are preserved in `C:/Users/roman/AppData/Local/Temp/pin-issue22-context-2ad88bfb/pytest-run3.log` and `pytest-run3.xml`.

The added `test_context_fault_snapshot_is_redacted_bounded_and_read_only` seeds synthetic persisted diagnostic rows directly through ORM models. It calls the real `_snapshot` composition and verifies:

- Delivery exposes one unknown and one started attempt, with the expected opaque IDs.
- Provider usage exposes one unknown and one started call, without request secrets or fabricated reported usage.
- Speech reports one failed and one processing request, with the failed job's opaque ID.
- Billing reports an expired-but-still-reserved usage hold, an unknown invoice, one unknown refund, one linked receipt, and the corresponding opaque order/reservation IDs.
- Returned ID lists stay within each projection's 25-item bound; private sentinel text, owner IDs, a provider-request sentinel, invoice payload, and synthetic user ID do not appear in the snapshot.
- The snapshot SQL listener rejects anything except `SELECT` and dialect-initialization `SHOW` statements. A before/after comparison of all inserted context fault facts is identical.

These are seeded-state projection and redaction checks; they do not exercise the delivery/provider/speech/billing producers that write those fault outcomes. The prior queue/worker and retention cases also passed in this module run.

## Fixture and cleanup

The fixture used for the passing run was `pin_issue22_ops_context_20261016_1ba8149e`, owned by `pin_test`. Before creation, the cluster identity was checked as PostgreSQL 18.6 (`180006`) at `127.0.0.1:51017`, role `pin_test`, maintenance DB `postgres`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. The full non-template catalog matched the exact 174-database baseline, the assigned name was absent, and the three protected databases were present. Post-create catalog was exactly the baseline plus this fixture (175 rows).

Before dropping the passing fixture, identity, owner, exact baseline-plus-fixture catalog, and zero active connections were rechecked. Cleanup dropped only this manifest-owned database. A separate post-cleanup catalog query found the exact original 174 entries, both task-owned fixture names absent, and all three protected databases present. The previous failed fixture `pin_issue22_ops_context_20261016_2ad88bfb` was separately checked and dropped after its own identity/owner/zero-connection/catalog checks; an independent query verified the original 174-row catalog before creating the passing fixture. Manifests, catalog snapshots, identity output, connection counts, create/drop logs, and audit receipts are retained in `C:/Users/roman/AppData/Local/Temp/pin-issue22-context-2ad88bfb/`.

Three harness attempts before the passing run are preserved there: (1) a post-create assertion compared PostgreSQL's `t` boolean rendering to PowerShell's `True`; (2) a strict manifest-property assignment stopped before test execution; and (3) an incorrect PowerShell JUnit argument caused pytest to collect zero tests. The next attempt used an unsupported `postgresql+asyncpg` URL and produced four fixture-setup errors before database connection or migration. That fixture was cleaned and the catalog restored before a new database was created. None of these attempts is counted as a test pass.

## Static checks

- `uv run --locked --offline --extra speech python -m ruff check tests/test_operations_postgres.py` — passed.
- `uv run --locked --offline --extra speech python -m mypy src/pin tests/test_operations_postgres.py` — passed, 220 source files checked.
- `uv run --locked --offline --extra speech python -m pytest --collect-only -q tests/test_operations_postgres.py` — 4 tests collected.
- `git diff --check` — passed.

Native command:

```text
uv run --locked --offline --extra speech python -m pytest -vv --junitxml=C:\Users\roman\AppData\Local\Temp\pin-issue22-context-2ad88bfb\pytest-run3.xml tests/test_operations_postgres.py
```

The test process used `PIN_TEST_OPERATIONS_DATABASE_URL=postgresql+psycopg://pin_test@127.0.0.1:51017/pin_issue22_ops_context_20261016_1ba8149e`; no credential was embedded.

## Limits

This proof covers local synthetic projection contents, redaction, bounds, and read-only snapshot behavior. It does not prove producer fault paths, actual provider or Telegram behavior, host alerts, restore, or whole Issue 22 acceptance. Low-disk reporting remains untested because this case did not alter the host or fake its resource projection. No payment/provider/network calls or host changes were made.
