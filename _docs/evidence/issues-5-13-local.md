# Ingress and conversation adapter verification

Date: 2026-10-13. Windows local-development scope; source base `2a40a5778ef6c48e8bab5cdc555bf69951510ec3` plus the ingress/model/billing facade candidate. Final committed revision is recorded in the issue comments when accepted. No push or deployment.

## Parent verification

- Ingress HTTP/polling and real PostgreSQL suite: `python -m pytest -q tests/test_telegram_ingress.py tests/test_telegram_ingress_postgres.py`, 19 passed in 10.10 seconds, chunk `0cfda3`. Callback rejection/network failure, commit-before-ack, payment routing, identity binding, duplicates and polling progress covered.
- Combined regression with all seven PostgreSQL environment variables configured: `python -m pytest -q`, 125 passed in 44.40 seconds, no skips, chunk `0279f6`. This run precedes the additional live-response accounting correction; rerun evidence must accompany that correction.
- `python -m ruff check src tests migrations`, `python -m mypy src` (61 files), and `python -m pin.architecture`: PASS, chunk `8c545e`. `git diff --check`: PASS.
- Fresh disposable database `pin_parent_candidate_37e8ce87`: Alembic upgrade head, check, downgrade base, upgrade head, check all passed with no drift. Single head `conversation_model_0001` after `telegram_ingress_0001`. Database dropped and absence verified, chunk `4aa134`.

The PostgreSQL 18.6 cluster is owned test infrastructure at `127.0.0.1:51017`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. The data directory was verified before creating or removing the uniquely named migration database. Existing production services were not used.

After the response-accounting correction added `conversation_model_0002`, the parent repeated the full head/base/head lifecycle and both drift checks on fresh database `pin_parent_corrected_c6f13d2e`. All passed; the database was dropped and absence verified (chunk `c157f0`). This additive migration preserves the already-recorded first live unknown call rather than resetting its database. The fresh lifecycle proof does not imply that downgrading a database containing partial usage facts is losslessly supported by the old schema.

## First live provider attempt: failed, not acceptance

Command: `python -m pin.bootstrap.model_smoke --live --owner-id ff337020-f5a2-427a-ac98-2d8c25c7d2c4`, with `PIN_DATABASE_URL` pointing to `pin_model_adapter_20261013` and the ignored local key loaded through bootstrap. Synthetic owner was explicitly provisioned with a genuine active billing trial period (250 AI turns, 900 voice seconds); no access bypass.

The catalog preflight passed. The bounded smoke stopped on the first Russian scenario with `SmokeBudgetExceeded: ru-text-create-task did not return a successful typed proposal`, exit 1, chunk `c274b5`. Exactly one started provider call exists for this owner; its state is `unknown`, category `usage_unknown`, with no persisted provider ID, token usage or cost. The user reservation was refunded with zero settled AI turns. No normalized result or plan was saved. The raw response was not retained, so its financial outcome cannot be reconstructed from this attempt. Unknown cost is not zero.

The first attempt exposed a gap: output-shape validation could discard available provider financial facts and a carried request ID. The executor is correcting that path with fixtures before any further completion call. A read-only official `/api/v1/key` check returned HTTP 200 and positive remaining key allowance; this does not prove any individual call cost. Aggregate key usage must not be attributed to this smoke without a prior baseline.

Issue #13 remains unaccepted until the current route yields validated RU/EN results with reconciled provider receipts. This failed attempt does not prove model accuracy or justify replacing the selected route.

## Corrected live attempt: partial capability proof

Implementation was committed as `811fe72d35cc94b6a63dbb95e628597dd3ea9686`; ingress is `9be51464c8fb345d43df75f83eff8ff654be81b2` and the crash-test readiness fix is `a03a487`. Parent focused model checks passed 33 tests, including four PostgreSQL cases (2.15 seconds, chunk `f342b4`); Ruff, mypy (62 files), architecture and preview passed (chunk `0824aa`). New prompt input bound is 19,532 tokens; standalone eight-call preview estimate is $0.00522016.

The parent used a temporary wrapper around the real live composition to reserve the first unknown attempt's $0.00063704 conservative exposure allowance, permit only seven additional calls, and cap their estimated exposure at $0.00456764. The overall authorized ceiling stays $0.01 and eight calls across attempts. No original uncertain request key was resent. The wrapper changes no product files or access policy.

Two additional calls completed and their real receipts were recorded:

| Synthetic case | Provider request ID | Prompt / completion tokens | Reported cost USD | Result |
|---|---|---|---|---|
| Russian task | `gen-1791886902-l563HtaS9gpQo8pAMQsH` | 519 / 123 | 0.000036294 | Valid create proposal, untimed task dated 2026-10-14 in Asia/Qyzylorda |
| English appointment | `gen-1791886905-M0QfGSY1mMGhU8Eq3FRZ` | 526 / 54 | 0.000026220 | Valid English reply, incorrectly refused creation without an existing plan |

Both receipts and normalized results are durable; each user turn settled once. No proposal was applied to a plan. Their reported total is $0.000062514; this is not the total batch cost because the first call remains unknown. The reported values differ from a naive catalog-price calculation, so actual provider receipts remain authoritative; no cause is inferred.

The smoke failed on the English proposal-kind gate (chunk `99164b`), and ledger inspection confirmed the rows/results above (chunk `263128`). The executor is clarifying that creation does not require an existing target; modification uses only current owner-filtered targets. Issue #13 remains open and unaccepted. Five calls remain under the aggregate eight-call bound; any new smoke must preserve the cumulative budget and prior unknown receipt.

## Limits

No Linux, Docker, hosted CI, deployment, real payment approval, end-to-end onboarding, broad AI accuracy benchmark, voice recognition or actual-host qualification was exercised. Ingress transport fixtures use real PostgreSQL and a fake Telegram API; the earlier owner-confirmed Telegram presentation proof remains separate. Local issue acceptance does not imply full MVP/release acceptance.

## Ingress acceptance on committed source

Parent verified a source snapshot of ingress/billing revision `9be51464c8fb345d43df75f83eff8ff654be81b2`, with only the crash-test IPC fix from `a03a487` added. Snapshot: `C:/Users/roman/AppData/Local/Temp/pin-ingress-9be5146-c1ca`; Python import provenance points to that snapshot, not the editable main checkout. Six fresh isolated PostgreSQL databases used the prefix `pin_parent_ingress9be_c1ca_` for UoW, migration, formatting, platform, billing, and ingress.

Initial snapshot verification found an empty-PID readiness race in the old crash test and missing callback tables in the parent-created formatting fixture. The test now atomically publishes its fully written PID; the formatting fixture was migrated before rerunning. Final full committed-snapshot suite passed **97 tests, no skips, in 41.77 seconds** (chunk `65d712`). Ruff, mypy (48 source files), and architecture passed (chunk `418fdf`). This isolated suite excludes unaccepted model code.

The exact ingress snapshot then passed Alembic head/check/base/head/check with no drift. All six owned databases were dropped and absence verified after checking cluster ownership (chunk `d72da2`). The source snapshot remains available for inspection. Direct Orchestrator verification satisfies #5's local transport AC and delivery conditions; GitHub closure/readback is recorded separately. Billing API metadata extension has real PostgreSQL reservation replay/concurrency proof in the same regression. No model or full MVP acceptance follows from this result.

Publisher read back #5 closed/completed with `status:accepted`: [evidence comment](https://github.com/dystrophyless/pin/issues/5#issuecomment-6143544899). [The #6 extension comment](https://github.com/dystrophyless/pin/issues/6#issuecomment-6143552806) records Billing API metadata proof while preserving its accepted state. No commits were pushed.

## Third model attempt and current boundary

Creation-clarification revision `c22401c8e79ad330971fba5c1e2c9de70caf42d6` passed parent full regression **131/131, no skips, 40.46 seconds** (chunk `8e6b2e`), Ruff/mypy 62 files/architecture (chunk `7f960b`). No schema change followed the previously verified `conversation_model_0002` migration in that correction.

The next distinct synthetic run reserved the entire original eight-call estimate ($0.00509632) for the first unknown outcome, plus already reported $0.000062514; five remaining-call slots had a new estimate $0.00333820, within the aggregate $0.01 ceiling. It produced a valid Russian task and then an invalid English proposal; the one repair produced an English refusal instead of an appointment proposal. The smoke exited 1 at its English proposal-kind gate (chunk `fcb3d3`).

| Provider request ID | Reported cost USD | Result |
|---|---|---|
| `gen-1791887418-XJzz3rjCe6xreKvFmwMt` | 0.000034620000 | Valid Russian task proposal |
| `gen-1791887421-aYG438gSyu6Oybc1lcQD` | 0.000031792900 | Invalid English output, retained financial receipt |
| `gen-1791887423-BW4zaHXEBR8TYCaztfLJ` | 0.000039560000 | Valid English reply, failed requested appointment outcome |

All five reported calls across later attempts sum to $0.0001684869; one original call remains unknown (ledger inspection chunk `dc197b`). Six calls have been used, leaving two under the aggregate eight-call bound. No plan was applied. The executor is adding grounded output schema and repair diagnostics before the final bounded proof. Issue #13 remains open; valid JSON replies alone do not prove English appointment creation.
