# Frozen local qualification: `9c474cf`

**Result: PASS for this Windows local qualification only.** The exact frozen source revision is `9c474cf8ac94eebec42bc8acf5965d9d55dccb96` with tree `5c5feebee8e369bba869ee65ca664596038069d2`. This is not feature acceptance, issue acceptance, overall MVP acceptance, or release acceptance.

## Frozen source and fixtures

The source was exported with `git archive` from the exact revision. The archive contains 478 tracked files, each recorded in `source-file-manifest.json`; import provenance resolved to the frozen `source/src/pin` tree. No tracked `.env.local` or unrelated `output/` content was included. Discovery and source/prefix guards passed for all 39 PostgreSQL opt-in keys, including the retention-restore source and target.

Before creation, the run verified the exact local PostgreSQL 18.6 cluster identity and all 39 assigned names absent. The complete non-template catalog matched the saved 174-database baseline by name, owner, and connection flag; all three protected databases were present. The run created 39 fresh manifest-owned fixtures.

## Results

- Frozen offline pytest: **569 passed, 0 failures, 0 errors, 0 skipped**, 362.21 seconds; five warnings. Raw console and JUnit are retained as `pytest.log` and `pytest.xml`.
- Ruff on `src tests landing`, mypy on 219 source files, architecture import boundaries, and frozen-source import provenance passed.
- The isolated Alembic lifecycle passed `head → current → check → base → head → check`; both drift checks reported no new operations.
- Landing passed all six unit tests, `node --check landing/app.js`, and the local destination validator.
- Cached offline Whisper passed the Russian and English fixtures and rejected silence. `uv --offline`, `HF_HUB_OFFLINE=1`, and `TRANSFORMERS_OFFLINE=1` were used; no model download occurred.
- The 50-owner dispatch test emitted an aggregate p95 of **2.845 seconds**. JUnit retained only that aggregate, not the per-owner delay vector. The recurring lifecycle case completed in **3.264 seconds**; its emitted reference, mutation, and revised-dispatch instants are in `post-junit-result.json`.
- The read-only pre-drop audit verified all 39 manifest fixtures belonged to `pin_test`, had connections enabled, and had zero active sessions. Cleanup dropped exactly those 39 manifest-recorded databases. Independent post-cleanup audit verified all assigned names absent, the exact 174-row non-template catalog restored, and the protected databases retained.
- Harness-only setup attempts are preserved: the first verify invocation lacked the frozen project's Python dependencies and stopped before running checks; the first landing unittest invocation had PowerShell quoting trouble and stopped before invoking unittest. Both were corrected, and the successful runs above used the frozen offline environment. No source changes were made during qualification.

No provider API, Telegram, payment/Stars, HTTP, external network, deployment, or model-spend call was made. The test adapters used local fixtures and injected transports. This local run does not establish live-service, host-alert, legal, or release readiness.

Complete raw logs, JUnit, commands, source and fixture manifests, version checks, and pre/post-cleanup catalog audits are preserved in `C:/Users/roman/AppData/Local/Temp/pin-final-frozen-local-qual-9c474cf-20261017-9726326fd2ad45539d110157a5e87e3f/`. The machine-readable suite result is `qualification-result.json`; post-run p95 and lifecycle details are in `post-junit-result.json`.

Root direct verification: inspected raw JUnit (569 tests, zero failures/errors/skips, 362.169 seconds), stage exit receipts, and independently recreated the Git archive to match all 478 tracked-file hashes against the recorded manifest and frozen tree. Independently queried the live PostgreSQL catalog after cleanup and matched every baseline name, owner and connection flag. The failed preceding full run remains historical failure evidence.
