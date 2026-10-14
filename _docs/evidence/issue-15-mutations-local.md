# Issue #15 mutation acceptance evidence

Accepted scope: Windows local development, recorded 2026-10-14 (Asia/Qyzylorda).

## Qualified source and environment

- Accepted source commit: `0213a5d289282c746bbb155aaeff3ee68bc39f3d`; source tree: `10a17bf3c26454d14655159efdfbb09140a8eb4c`.
- Evidence packet: `C:/Users/roman/AppData/Local/Temp/pin-parent-mutation-voice-20261014-1518d`, frozen from the exact accepted source. The live checkout had concurrent uncommitted executor work; acceptance uses the archived commit and tree, not unrelated dirty changes.
- Windows 11 build 26200, Python 3.12.12, PostgreSQL 18.6 at `127.0.0.1:51017` (`pin_test`), using the project lock and offline `speech` extra. The cluster data directory was `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`.

Archived qualification commands:

```text
python freeze_archive.py 0213a5d289282c746bbb155aaeff3ee68bc39f3d
uv run --project source --locked --offline --extra speech python -c <source_guards.validate_source against frozen source>
uv run --project source --locked --offline --extra speech python create.py
uv run --project source --locked --offline --extra speech python migrations.py
uv run --project source --locked --offline --extra speech python verify.py
uv run --project source --locked --offline --extra speech python speech.py
uv run --project source --locked --offline --extra speech python cleanup.py
```

The packet preserves logs, JUnit, fixture manifests, raw result JSON, migration results, and cleanup proof. It created 23 fresh guarded databases; two separately reserved regression fixtures were preserved. Cleanup verified all 23 packet databases absent.

## Acceptance evidence

Issue #15 scope, acceptance criteria and delivery conditions were checked against the qualified source and archived artifacts by the Orchestrator. The full archived qualifier itself reported PASS/exit 0: **399 passed, 0 failed, 0 errors, 0 skipped** in 189.79 seconds. Ruff, mypy with `--no-incremental` (152 files), architecture, and the six-step head/current/check/base/head/check migration lifecycle all passed with zero schema drift. Archived actual Russian and English fixtures were recognized; decoded audio durations were 4.169 and 3.149 seconds, and silence was rejected. Those are clip durations, not runtime or performance measurements; no speed or capacity claim is made.

Fifty focused PostgreSQL cases were included: mutation core 5, runtime 12, actual-application mutation 10, native delivery 4, home-zone coordinator 13, and native settings callbacks 6. They exercised durable owner/version-bound previews and opaque 15-minute confirmation; deterministic commands/menus and AI proposal handoff; no plan mutation before confirmation; pagination beyond 50 items; stale, cross-owner, and corrected-target rejection; appointment duration/end/reminder preservation and explicit clarification; and savepoint rollback of usage, receipts, and events after failure even if the caller commits.

Mutation and consumer evidence verified occurrence-only Done with no reopening, stable reschedule occurrence identity, and count/history preservation for the June 1/15/29 series moved to June 8/22 and July 6. Future cancellation targets exactly the generated open occurrences and stops generation atomically. Native delivery suppresses already-claimed obsolete Cancel/Done/Reschedule reminders, sends the current version-2 reminder after rescheduling, and truthfully reports a send that began before cancellation without creating future intent.

Home-zone cases verified that explicit reinterpretation affects only the selected items, Keep behavior preserves unselected schedules and event zones, and history/count, untimed items, DST behavior, and stopped series are preserved. Missing legacy reminders require an explicit choice or a bounded rejection. Billing `RESCHEDULE_PLAN` access is enforced for selected home-zone occurrence/series/reminder changes both at preview and locked confirmation, before proof consumption or business writes. Real expired-access and expiry-between-preview-and-confirmation cases verified no write.

Accountability cycle close/supersede integration remains Issue #16's responsibility and is not claimed as part of this Issue #15 acceptance. The older packet's raw wrapper aggregation defect remains preserved as historical evidence; the accepted `1518d` packet is the later exact-source run whose qualification wrapper itself passed.

## Qualification limits

This accepts the assigned Windows local-development increment. It does not claim live provider or Telegram qualification, host speed/capacity, Docker, deployment, public rollout, push, or overall MVP acceptance. The speech transport and provider integrations were injected where the tests required them; no live provider calls were added.
