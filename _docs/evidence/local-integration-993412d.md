# Frozen local qualification: 993412d

**Result: PASS for this Windows local qualification only.** The frozen product source is commit `993412de4c4cfba04d136a84484b938418f81f21` (tree `e3a883ed42611b0382f1a0b77ec428d528b6122e`). This evidence is not feature acceptance, overall MVP acceptance, or release acceptance.

## Frozen source and environment

The source archive was created with `git archive` from the exact commit. Its SHA-256 is `96b5633884eddd103485e516cacc35849d9778195673b049310aa11b84045b61`; `source-file-manifest.json` records SHA-256 for each of the 452 tracked files. Import provenance resolves to `source/src/pin/__init__.py` inside the packet. Untracked repository `output/`, environment files, and credentials were excluded.

The run used Windows, CPython 3.12.12, the frozen lockfile, and `uv run --locked --offline`. PostgreSQL identity was checked before fixture creation: version 18.6 (`server_version_num=180006`), `127.0.0.1:51017`, role `pin_test`, maintenance database `postgres`, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. The initial catalog contained exactly 174 non-template databases, including the protected model-adapter and Issue 20 databases.

All 36 database opt-ins discovered in the frozen tests received distinct, fresh database names with the required test prefixes. The three added lifecycle opt-ins used `pin_issue25_core_993412d`, `pin_issue20_support_993412d`, and `pin_issue26_paid_993412d`. Preflight confirmed every assigned name absent before creation. See `preflight.json`, `databases.json`, `fixture-manifest.json`, and `create-result.json`.

## Verification results

- Full locked offline pytest: **549 passed, 0 failed, 0 errors, 0 skipped** in 303.036 seconds. JUnit is `pytest.xml`; full console output is `pytest.log`. Both fixed-clock core lifecycle cases (English and Russian) passed with reference instant `2026-10-13T09:00:00+00:00` in `Asia/Almaty`; each completed its occurrence with zero outstanding delivery jobs. Four warnings remain: two `record_property`/`xunit2` compatibility warnings on those cases, and two `pytest-asyncio` event-loop policy deprecation warnings.
- Ruff passed over `src tests` and `src tests landing` (`ruff.log`, `ruff-whole.log`). Mypy passed over `src` with no issues in 219 files (`mypy.log`). Architecture import boundaries passed (`architecture.log`). Formatting/model-test pre-migrations and frozen-source import provenance also passed; see `qualification-result.json` and `pre-migrate-*.log`.
- Alembic migration lifecycle passed: `head → current → check → base → head → check`. Both schema drift checks were clean; raw command logs are `migration-0.log` through `migration-5.log`, and the summary is `migration-result.json`.
- The full suite measured actual outer-loop transport-dispatch-start p95 for 50 due owners at **2.091 seconds**, below the unchanged 10-second threshold. This is local synthetic/injected-transport performance evidence. The JUnit test case took 5.775 seconds; see `p95-result.json` and `pytest.xml`.
- Cached offline Whisper small passed on the Russian and English audio fixtures; silence was rejected. The model was already cached, offline flags were set, and no download occurred. See `speech-result.json` and `speech.log`.
- Landing validation passed all six unit tests, JavaScript syntax validation, and the local destination validator. Both configured targets are `https://t.me/pin_appbot`. These checks establish local configuration and page structure, not live reachability or deployment. See `landing-result.json` and its referenced logs.

## Cleanup and limits

Before cleanup, all 36 manifest fixture names were rechecked against the exact cluster; each was owned by `pin_test`, allowed connections, and had zero active connections (`precleanup-fixture-audit.json`). Cleanup dropped exactly those 36 manifest-owned databases. An independent read-only audit found all 36 absent and the non-template catalog exactly restored to its initial 174 entries. Protected historical databases remain present. See `cleanup-result.json` and `post-cleanup-independent-audit.json`.

No provider calls, live Telegram calls, actual Stars payment testing, deployment, Docker, network model downloads, or additional model spend occurred. The result supports local synthetic behavior only; it does not establish external provider operation, live service reachability, legal retention compliance, host alerting, staging behavior, or production readiness. Actual Stars testing remains removed from required acceptance.

The raw packet is `C:/Users/roman/AppData/Local/Temp/pin-final-frozen-local-qual-993412d-20261015`; `qualification-receipt.json` maps commands and exits. This qualification does not decide issue acceptance.
