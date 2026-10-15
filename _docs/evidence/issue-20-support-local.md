# Issue 20: persisted terms and payment-support routes

## Scope and evidence boundary

This local addition proves that `/terms`, `/acceptterms`, `/buy`, and `/paysupport` travel through PostgreSQL Telegram ingress, the owner-bound application job, and the persisted reply outbox in Russian and English. It checks durable owner mapping, terms view/acceptance, guarded order creation, and support copy against the saved records and the reply caused by each processed job.

The owner removed actual Telegram Stars payment testing from required verification and acceptance: “Проверку оплаты не откладываем, а убираем.” This packet contains no Telegram/provider calls, real receipt, payment, cancellation, refund, model request, or paid resource use. A locally created order remains queued and unconfirmed. Synthetic-event billing lifecycle proof is covered separately; this route proof does not establish provider operation or issue acceptance.

## Scenario

`tests/test_payment_support_routes_postgres.py` seeds two isolated Telegram owners with RU/EN preferences and trial access, persists their commands with `persist_update`, claims each actual application job, and runs `process_application_job`. For every command it reads the succeeded job and its single `telegram.reply.requested` outbox event by causation ID and owner, then checks the durable inbox-to-owner and reply-to-chat binding.

The scenario reads RU and EN terms for 250 Stars per 30 days, automatic renewal, cancellation preserving paid-through access, 1,000 AI turns and 60 voice minutes without rollover, manual refund support, and the explicit acceptance step. An English owner who has not opened terms cannot accept them. The Russian owner reads terms but cannot create an order; no order, receipt, or paid period is persisted. After the English owner reads and accepts the current revision, `/buy` persists a queued order and explicitly says that no payment is confirmed; it creates no receipt or paid period. Both languages' `/paysupport` reply requests the Telegram charge ID, directs the user to manual operator review, and rejects bank-data submission.

## Native PostgreSQL run

Executed 2026-10-15 at the dirty local source state based on `6519e7495484f9aee80cd148bedc650fa8d21843`. The test source SHA-256 was `366B4E22A4014301F0C8E79A27D4D94D34C8A49D32B442B1C893CDAAB94AA06C`. This task added only the three assigned paths; the parallel paid-lifecycle test and pre-existing `output/` artifact were left untouched.

The assigned database was `pin_issue20_support_20261015_routes_2d6f`. Before creation, the root `postgres` database was verified on `127.0.0.1:51017`, user `pin_test`, PostgreSQL `18.6` / `180006`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`; the target name was absent and its non-manifest database inventory was recorded. The fixture independently enforces this identity and refuses protected database names. The manifest and cleanup receipt are in `C:/Users/roman/AppData/Local/Temp/pin-issue20-support-local-20261015/fixture-manifest.json`.

Run from the repository root with the assigned disposable database URL in the process environment, without echoing it. The exact successful command was:

```powershell
$env:PIN_TEST_ISSUE20_SUPPORT_DATABASE_URL = 'postgresql+psycopg://pin_test@127.0.0.1:51017/pin_issue20_support_20261015_routes_2d6f'
uv run --frozen pytest -q tests/test_payment_support_routes_postgres.py --junitxml 'C:/Users/roman/AppData/Local/Temp/pin-issue20-support-local-20261015/routes.junit.xml'
```

Result: **1 passed, 0 failed, 0 errors, 0 skipped**, JUnit elapsed `9.557s` (test case `1.409s`), exit code 0. Ruff, mypy on the test file, and `git diff --check` passed. Two earlier failed-run logs and JUnit files are preserved as `routes-attempt-1-fail.*` and `routes-attempt-2-fail.*`; each captured a test assertion correction (outbox stores an EventEnvelope and generic RU support says refunds are reviewed manually), not a product failure. The successful rerun used the same manifest-owned database after those attempts; each invocation generated new owner IDs, and the earlier command jobs had completed. The final run exercised all eight commands.

Cleanup rechecked the exact cluster identity and fixture database owner, verified zero active connections, dropped only the manifest-owned database, then confirmed target absence, all three protected databases present, and the non-manifest database set unchanged. No unowned database or repository artifact was cleaned.

## Result

This proves native local command-route persistence only. It is not proof of actual Telegram provider operation, a paid receipt, refund execution, issue acceptance, or whole-issue completion. No Stars payment was tested. Runbook: [`payments_local.md`](../runbooks/payments_local.md).
