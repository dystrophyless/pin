# Frozen local qualification: `d0972d3`

**Result: FAIL for this Windows local qualification only.** The exact frozen source revision is `d0972d32c1e524ff2a732b1248bb49dd461707e6` with tree `f09b4dae5e8795b5346bf58e41ace129421745ed`. This is not feature acceptance, issue acceptance, overall MVP acceptance, or release acceptance.

## Frozen source and fixtures

The source was exported with `git archive` from the exact revision. The archive contains 476 tracked files, each recorded in `source-file-manifest.json`; import provenance resolved to the frozen `source/src/pin` tree. No tracked `.env.local` or unrelated `output/` content was included.

Discovery found 39 PostgreSQL opt-in keys, including the retention-restore source and target. Source and prefix guards passed. Before creation, the exact local cluster identity/version/data directory and all 39 assigned names were checked; none existed. The complete non-template catalog matched the saved exact 174-database baseline, including owner and connection flags, and all three protected databases were present. The run created 39 fresh manifest-owned fixtures.

## Results

- Frozen offline pytest: **569 collected; 567 passed, 2 failed, 0 errors, 0 skipped**, 300.09 seconds; five warnings. Raw console and JUnit are retained as `pytest.log` and `pytest.xml`.
- The two failures were `test_private_core_lifecycle_runs_through_real_producers_and_consumers[en]` and `[ru]` in `test_core_lifecycle_integration_postgres.py`, both with the traceback reaching line 792. Their full traces are preserved in JUnit and the console log. No runtime or test contract was changed during this verification assignment.
- Ruff on `src tests` and `src tests landing` reported one E501 in `src/pin/contexts/conversation/application/service.py:121` (101 characters). Mypy passed for 219 source files; architecture import boundaries and frozen-source import provenance passed.
- The isolated Alembic lifecycle passed `head → current → check → base → head → check`; both drift checks reported no new operations.
- Landing passed all six unit tests, `node --check landing/app.js`, and the local destination validator.
- Cached offline Whisper passed the Russian and English fixtures and rejected silence. `uv --offline`, `HF_HUB_OFFLINE=1`, and `TRANSFORMERS_OFFLINE=1` were used; no model download occurred.
- The 50-owner dispatch test emitted an aggregate p95 of **2.169 seconds**. JUnit retained only that aggregate, not the per-owner delay vector. The recurring lifecycle case completed in **2.355 seconds**; its emitted reference, mutation, and revised-dispatch instants are in the JUnit summary packet.
- The read-only pre-drop audit verified all 39 manifest fixtures belonged to `pin_test`, had connections enabled, and had zero active sessions. Cleanup dropped exactly those 39 manifest-recorded databases. Independent post-cleanup audit verified all assigned names absent, the exact 174-row non-template catalog restored, and the protected databases retained.

No provider API, Telegram, payment/Stars, HTTP, external network, deployment, or model-spend call was made. The test adapters used local fixtures and injected transports. This local run does not establish live-service, host-alert, legal, or release readiness.

Complete raw logs, JUnit, commands, source and fixture manifests, version checks, and pre/post-cleanup catalog audits are preserved in `C:/Users/roman/AppData/Local/Temp/pin-final-frozen-local-qual-d0972d3-20261017-d0ae66c4f4cf4946b450206e3217b058/`. The full machine-readable pytest result is `current-junit-summary.json`; `qualification-result.json` records the failed test and Ruff checks alongside passing mypy and architecture checks.
