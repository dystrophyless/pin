# Ingress and conversation adapter verification

Date: 2026-10-13. Windows local-development scope; source base `2a40a5778ef6c48e8bab5cdc555bf69951510ec3` plus the ingress/model/billing facade candidate. Final committed revision is recorded in the issue comments when accepted. No push or deployment.

## Parent verification

- Ingress HTTP/polling and real PostgreSQL suite: `python -m pytest -q tests/test_telegram_ingress.py tests/test_telegram_ingress_postgres.py`, 19 passed in 10.10 seconds, chunk `0cfda3`. Callback rejection/network failure, commit-before-ack, payment routing, identity binding, duplicates and polling progress covered.
- Combined regression with all seven PostgreSQL environment variables configured: `python -m pytest -q`, 125 passed in 44.40 seconds, no skips, chunk `0279f6`. This run precedes the additional live-response accounting correction; rerun evidence must accompany that correction.
- `python -m ruff check src tests migrations`, `python -m mypy src` (61 files), and `python -m pin.architecture`: PASS, chunk `8c545e`. `git diff --check`: PASS.
- Fresh disposable database `pin_parent_candidate_37e8ce87`: Alembic upgrade head, check, downgrade base, upgrade head, check all passed with no drift. Single head `conversation_model_0001` after `telegram_ingress_0001`. Database dropped and absence verified, chunk `4aa134`.

The PostgreSQL 18.6 cluster is owned test infrastructure at `127.0.0.1:51017`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. The data directory was verified before creating or removing the uniquely named migration database. Existing production services were not used.

## First live provider attempt: failed, not acceptance

Command: `python -m pin.bootstrap.model_smoke --live --owner-id ff337020-f5a2-427a-ac98-2d8c25c7d2c4`, with `PIN_DATABASE_URL` pointing to `pin_model_adapter_20261013` and the ignored local key loaded through bootstrap. Synthetic owner was explicitly provisioned with a genuine active billing trial period (250 AI turns, 900 voice seconds); no access bypass.

The catalog preflight passed. The bounded smoke stopped on the first Russian scenario with `SmokeBudgetExceeded: ru-text-create-task did not return a successful typed proposal`, exit 1, chunk `c274b5`. Exactly one started provider call exists for this owner; its state is `unknown`, category `usage_unknown`, with no persisted provider ID, token usage or cost. The user reservation was refunded with zero settled AI turns. No normalized result or plan was saved. The raw response was not retained, so its financial outcome cannot be reconstructed from this attempt. Unknown cost is not zero.

The first attempt exposed a gap: output-shape validation could discard available provider financial facts and a carried request ID. The executor is correcting that path with fixtures before any further completion call. A read-only official `/api/v1/key` check returned HTTP 200 and positive remaining key allowance; this does not prove any individual call cost. Aggregate key usage must not be attributed to this smoke without a prior baseline.

Issue #13 remains unaccepted until the current route yields validated RU/EN results with reconciled provider receipts. This failed attempt does not prove model accuracy or justify replacing the selected route.

## Limits

No Linux, Docker, hosted CI, deployment, real payment approval, end-to-end onboarding, broad AI accuracy benchmark, voice recognition or actual-host qualification was exercised. Ingress transport fixtures use real PostgreSQL and a fake Telegram API; the earlier owner-confirmed Telegram presentation proof remains separate. Local issue acceptance does not imply full MVP/release acceptance.
