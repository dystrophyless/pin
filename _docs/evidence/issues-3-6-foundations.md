# Durable work and access: local verification

Date: 2026-10-13 (Asia/Qyzylorda). Scope: issues [#3](https://github.com/dystrophyless/pin/issues/3) and [#6](https://github.com/dystrophyless/pin/issues/6). Implementation revision: the commit containing this evidence packet and the corresponding platform/billing changes; record its exact SHA in the issue acceptance comments. No push is authorized.

Environment: Windows, locked CPython 3.12.12 / uv 0.10.4, PostgreSQL 18.6. Tests use the isolated loopback cluster at port 51017. Its data directory was checked against `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013` before database creation/reset. The unrelated installed PostgreSQL service was not modified.

## Durable work (#3)

`tests/test_platform_work_postgres.py`: executor's final targeted run passed 8 tests against real PostgreSQL. Coverage includes concurrent update deduplication and claimers, lease renewal/reclaim, stale-token rejection for effect receipts/continuation jobs/outbox writes, per-owner speech/text ordering with independent owners and delivery progress, poison retries, out-of-order event reconciliation and transactional consumer receipt rollback.

Actual child processes exit after committing a claim and after committing an effect receipt before completing the job. Recovery inspects persisted claims/receipts/effects and proves a single committed effect. Distinct stale event identities can be recorded without relaxing pending/applied version protection. No production claim filter was added to compensate for test residue.

Contract and consumer responsibilities: [`platform-work.md`](../platform-work.md).

## Access and usage (#6)

Parent command: `uv run --locked --offline pytest -q tests/test_billing_access.py tests/test_billing_access_postgres.py`, with `PIN_TEST_BILLING_DATABASE_URL` pointing to the owned disposable billing database. Result: 22 passed in 2.38 seconds, including six real PostgreSQL scenarios.

Coverage includes exact trial/paid windows and operation rights, concurrent final-unit reservations, atomic AI/decoded-voice dimensions, immutable request/period identity, duplicate and terminal settlement/refund, deadline expiry, old-period replay during a fresh paid period, and deleted/missing account denial. A replay after expiry returns the original terminal reservation and reports current entitlement separately; it neither creates a new reservation nor charges the new period.

The bounded expiry primitive, lock order and caller recovery responsibilities are documented in [`access-usage.md`](../access-usage.md). Deadlines are caller supplied and capped by the period end; no new product timeout or retention lifetime was invented.

## Shared migration and regression checks

Parent fresh database `pin_parent_foundations_bd7578d2`: upgrade to head, Alembic drift check, downgrade to base, re-upgrade to head, repeat drift check all passed. Both checks reported `No new upgrade operations detected`. The database was dropped afterward and its absence verified. Migration order: persistence -> Telegram callback tokens -> platform work -> billing access.

Parent full-source mypy passed for 44 files, Ruff passed across source/tests/migrations, and `git diff --check` passed. The initial full PostgreSQL-enabled suite reported 74 passed and one failure: the existing Telegram smoke helper read environment variables outside the bootstrap boundary. The loader was moved to bootstrap configuration, its token uses `SecretStr`, and environment/local-file precedence coverage was added. The final parent full-suite rerun passed **77 tests in 17.89 seconds with no skips**. Architecture checks are included; no rule was weakened or bypassed. No additional Telegram sends were made for this fix.

Acceptance source: the owner delegated per-issue acceptance/closure and removed implementation Reviewer agents for this local phase. Based on the executed checks, migration/cleanup evidence, published contracts and scoped implementation, the Orchestrator accepts #3 and #6 for Windows local development. Record closure/readback in GitHub; this file alone does not assert a remote status change.

## Boundaries

These foundations do not implement onboarding/trial activation, real payments, model inference, transcription, business plan/reminder rules, Telegram ingress, external delivery, or an automatically running expiry/reconciliation scheduler. Feature owners must wire the published contracts and preserve their transaction/fencing requirements. Windows local evidence does not establish Linux, Docker, hosted CI, provider accuracy/cost/latency, deployment, production restores or full MVP/release acceptance. Those gates remain deferred or pending under the owner's local-development scope.
