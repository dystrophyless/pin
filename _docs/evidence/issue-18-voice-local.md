# Issue #18 local acceptance evidence

Accepted scope: Windows local development, recorded 2026-10-14 (Asia/Qyzylorda).

## Qualified source and environment

- Accepted source commit: `83d9b86e049080bcf51f8d54552f23cfc01f7a65`; source tree: `1db33b0b385ce541985e990633595a2d202c161e`.
- Evidence was captured from the archived source packet at `C:/Users/roman/AppData/Local/Temp/pin-parent-mutation-voice-20261014-1518c`, using the accepted source and fresh guarded databases. The live checkout has concurrent uncommitted work owned by other executors; this acceptance does not attribute those dirty changes to the qualified source.
- Windows 11 build 26200, Python 3.12.12, PostgreSQL 18.6 on `127.0.0.1:51017` (`pin_test`). Speech used cached offline multilingual `small`, CPU INT8, with faster-whisper 1.2.1, PyAV 19.0.1, and NumPy 2.5.4.

The archived packet froze the qualified commit, created the 23 guarded fixtures, then ran the checks and cleanup with the locked offline speech extra:

```text
python freeze_archive.py 83d9b86e049080bcf51f8d54552f23cfc01f7a65
uv run --project source --locked --offline --extra speech python create.py
uv run --project source --locked --offline --extra speech python migrations.py
uv run --project source --locked --offline --extra speech python verify.py
uv run --project source --locked --offline --extra speech python speech.py
uv run --project source --locked --offline --extra speech python cleanup.py
```

The packet retains per-step logs and manifests in `C:/Users/roman/AppData/Local/Temp/pin-parent-mutation-voice-20261014-1518c`, including `pytest.xml`, `qualification-result.json`, and `result-reconciliation.json`.

## Acceptance evidence

- Issue #18 scope, acceptance criteria and delivery conditions were checked against the qualified source and artifacts by the Orchestrator.
- Archived actual-import full suite: **394 passed, 0 failed, 0 errors, 0 skipped** in 186.56 seconds, including 15 PostgreSQL voice tests, 22 Speech unit tests, and 3 prepared-capture tests. Twenty-three fresh guarded databases were used.
- Ruff, mypy (`--no-incremental`, 152 checked), and architecture checks passed. Migration head/check/base/head/check passed without drift. The 23 owned databases were cleaned up and verified absent.
- RU and EN fixtures were recognized with decoded durations of 4.169s and 3.149s; silence was rejected. These are audio clip durations, not performance measurements, and support no speed/capacity claim. Decoder, streamed-file, and trusted-download checks covered 179/180/>180 seconds, below/at/above 10 MB, misleading declared metadata, corrupt input, and arbitrary URL rejection. Quota rejection, reservation/refund/settlement, heartbeat/reclaim/delete fencing, and immediate/24-hour crash-file cleanup were exercised.
- Four actual spawned-process termination/restart cases covered a downloaded private-file orphan, durable transcript, durable provider-call-start without a result, and final atomic continuation/settlement. Unknown provider outcome is retained as cost evidence, refunded without provider resubmission, and protected by stale-claim fencing; the final boundary produces one capture, result, ledger terminal outcome, and job.
- Telegram download and AI transport were injected. Earlier real Telegram formatting/transport acceptance remains foundation evidence, not proof of an actual user voice message through a live bot.

## Qualification limits and raw wrapper issue

This accepts the assigned Windows local-development increment. It does not claim Kazakh recognition, host-speed/capacity qualification, payment/provider qualification, Docker, public rollout, deployment, push, or overall MVP acceptance. No additional live OpenRouter request was made; the existing eight-call/$0.01 window remains exhausted.

The archived `qualification-result.json` and wrapper exit code report failure because three successful setup checks omitted pass flags. Their underlying commands exited 0; the Orchestrator checked the JUnit/logs and `result-reconciliation.json`, which records the underlying checks as passing. The raw wrapper failure is preserved and is not represented as an original wrapper PASS.
