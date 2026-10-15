# Frozen local qualification: 12aa6b5

**Result: FAIL for this local qualification.** The exact frozen source revision is `12aa6b5f9b3e69373780364ba0341954bbfec74e` (tree `7d37d319bc1b8aeb87088b2766454801faa32683`). This document records a local qualification result; it is not feature acceptance, overall MVP acceptance, or release acceptance.

The source was created with `git archive`; its SHA-256 is `2e9555b07a463465e0022c1226a4103168b8f8eaa191ad1d6406b13a1c92dbec`. The archive contains 451 tracked files, with individual hashes in `source-file-manifest.json`. Import provenance points to the frozen `source/src/pin/__init__.py`. The unrelated repository `output/` and ignored `.env.local` were not copied into the archive.

The Windows run used the locked offline environment and PostgreSQL 18.6 (`server_version_num=180006`) on `127.0.0.1:51017`, role `pin_test`, maintenance database `postgres`, and data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. The preflight snapshot contained 174 non-template databases. All 36 discovered test database URL opt-ins received fresh names and their source-required prefixes, including the new core lifecycle, Issue 20 support, and paid lifecycle suites.

## Results

- Full frozen offline pytest: **FAIL**, 547 passed, 2 failed, 0 errors, 0 skipped, in 302.77 seconds. Four warnings were recorded. Both failures are the English and Russian cases in `tests/test_core_lifecycle_integration_postgres.py`; line 761 expects no pending or leased delivery-lane jobs after completion.
- A read-only, no-payload inspection of the owned core fixture found one pending `notifications.deliver_recovery_summary` job for each failed synthetic owner. Each was created after a `billing.access_period.activated` event consumed by `telegram.access_resume`. That handler enqueues a recovery summary available one hour later. The remaining jobs were delayed access-resume summaries, not reminder or accountability jobs; the reminder intents and accountability cycle reached their terminal states. The preserved diagnostic is `core-job-correlations.json`; the full traceback is in `pytest.log`.
- Ruff over `src`, `tests`, and `landing`: **PASS**. Mypy over `src`: **PASS**, 219 source files. Architecture import boundaries: **PASS**.
- Migration lifecycle: **PASS**, `head → current → check → base → head → check`; both schema drift checks found no operations. Raw output is in `migration-0.log` through `migration-5.log`.
- The 50-owner dispatch-start p95 was **2.225 seconds**, passing the existing 10-second threshold. This value came from the full suite's JUnit property; the testcase duration was 6.323 seconds.
- Cached offline Whisper small: **PASS** for Russian and English audio; silence was rejected. No model download or provider call was made.
- Landing: **PASS** for six unit tests, JavaScript syntax, and the configured destination validator. Both local targets are `https://t.me/pin_appbot`. The validator checks local configuration and links; it does not establish live service availability or deployment.
- Cleanup: **PASS**. All 36 manifest-owned fixtures were dropped after the cluster identity, ownership, and active connections were checked. An independent read-only audit found every assigned name absent and the exact pre-run nonmanifest catalog restored to 174 databases. Both protected Issue 20 sandboxes and `pin_model_adapter_20261013` remain present.

The two core lifecycle failures remain findings for the next revision. This qualification did not change product source or test assertions. Actual Stars payment testing remains removed from required acceptance; no payment API, Telegram bot message, provider call, Docker, deployment, or network model download occurred.

The complete packet, including command and exit receipts, fixture manifest, JUnit, raw logs, source hash manifest, no-payload job diagnostics, and cleanup catalog audit, is at `C:/Users/roman/AppData/Local/Temp/pin-final-frozen-local-qual-12aa6b5-20261015`.
