# Issue 25 core lifecycle clock regression

**Result: focused local regression checks pass.** On 2026-10-17 (Asia/Qyzylorda), the EN and RU core lifecycle PostgreSQL cases both pass on the working tree based on `d0972d32c1e524ff2a732b1248bb49dd461707e6`.

The earlier full local run failed only at the future-sweep assertion in these two cases. The test anchored its simulated clock at 2026-10-13 09:00 UTC, while `platform.outbox.created_at` is populated by PostgreSQL `now()`. The delivery worker considers reply events only when `created_at <= sweep_time`. That made the historical simulated sweep date dependent on when the real test ran; replies created after that cutoff remained queued until the next sweep. This is test-clock drift, not a production delivery change.

The test now anchors the scenario at 09:00 UTC on the current UTC date, advancing to the next date if 09:00 has passed. This retains the original 09:00 UTC / 14:00 local daytime and all relative schedule offsets. The assertion that no replies appear on the future sweep remains unchanged. In `service.py`, the long few-shot prompt literals were wrapped without changing `_SYSTEM`; an AST comparison against the base revision confirmed all 6,490 prompt characters are identical.

The EN/RU core cases were run after the clock change. After the final prompt-literal wrapping, the model-adapter tests, Ruff, and AST prompt-content comparison were rerun; that formatting-only edit preserves the exact prompt string.

Verification:

- `tests/test_core_lifecycle_integration_postgres.py -k test_private_core_lifecycle_runs_through_real_producers_and_consumers`: 2 passed, 0 failed; two existing pytest `record_property`/JUnit-family warnings.
- `tests/test_model_adapter.py`: 47 passed, 0 failed.
- `ruff check src tests landing`: passed.
- Prompt-content comparison: passed.
- `git diff --check`: passed.

The focused test used one fresh `pin_issue25_core_` database on the assigned local PostgreSQL 18.6 cluster. Before cleanup it was verified as owned by `pin_test`, connections enabled, and with zero active sessions. Only that manifest database was dropped. Independent post-cleanup inspection confirmed the exact 174-database baseline and all three protected databases were preserved.

Raw focused logs, JUnit files, commands, fixture manifest and catalog audits are in `C:/Users/roman/AppData/Local/Temp/pin-core-clock-regression-7aba94412d4d479fa9e8adff379028be`. The prior full-run failure evidence in [`local-integration-d0972d3.md`](local-integration-d0972d3.md) remains unchanged. The full suite was not rerun after this focused fix, so this result does not revise its full-suite status or establish release readiness.
