# Issue 23 local restore rehearsal — local proof

**Status: ONE LOCAL NATIVE SCENARIO PASSED; NOT ISSUE ACCEPTANCE.** On 2026-10-16, after the root granted the sole fixture slot, the composed rehearsal passed on the assigned local PostgreSQL cluster. All records and transport outcomes were synthetic. It does not prove a live provider response or hosted/off-host restore.

## Contract and composed scenario

The retention runbook requires the independently retained deletion artifact to be replayed successfully after restoring an older database snapshot and before Telegram ingress, application, delivery, or speech workers start. The existing code exposes the actual deletion confirmation/export/purge producer, `pin-retention replay`, and the delivery worker's expired-lease recovery for a persisted `started` attempt. Existing local component tests cover deletion artifact replay and no-blind-resend lease recovery separately; they do not exercise both against a real older `pg_dump` archive and a restored target.

The opt-in test composes those contracts with two fresh manifest-owned databases. It seeds one victim owner with operational plan data and retained trial/paid identity facts, plus a separate owner with a due reminder. It blocks a synthetic fake transport at the send boundary after the delivery attempt is persisted as `started`, then takes a custom-format `pg_dump`. After that snapshot, it confirms and exports deletion through the retention producer and lets the fake transport return an unknown outcome. The test restores only into the empty target with `pg_restore`, verifies the snapshot still has the pre-deletion plan and started delivery, and invokes the actual `pin-retention replay` CLI before starting the delivery worker.

The replay checks missing and malformed artifacts fail closed without deleting the restored plan, then replays the independently retained valid artifact twice. It checks deleted operational state stays deleted while trial, paid-period and retained billing-identity facts survive and the unrelated owner's plan remains. Only after successful replay does it expire the restored reminder claim and run the actual delivery iteration. The target must mark the pre-snapshot started attempt unknown without a second fake transport call or an additional attempt.

The transport events are synthetic local test events. The scenario starts with a persisted pre-snapshot `started` attempt; it does **not** cover a send attempt absent from the backup or reconciliation of every post-backup provider/financial fact. It does not prove a Telegram/provider response, process-host outage, off-host backup durability, encryption, RPO/RTO, or a hosted restore. The test enforces operator ordering inside its scenario; the runbook remains an operator procedure, not an automatic ingress/worker startup fence. The operational restore projection remains `unavailable`.

## Fixture and native command

The module requires these explicit, password-free DSNs and refuses any other identity:

- `PIN_TEST_RETENTION_RESTORE_SOURCE_DATABASE_URL=postgresql+psycopg://pin_test@127.0.0.1:51017/pin_issue23_restore_source_<unique>`
- `PIN_TEST_RETENTION_RESTORE_TARGET_DATABASE_URL=postgresql+psycopg://pin_test@127.0.0.1:51017/pin_issue23_restore_target_<unique>`

For this run the root approved the exact names `pin_issue23_restore_source_20261016f7ad49b1` and `pin_issue23_restore_target_20261016f7ad49b1`. The outer manifest created and later dropped only those two databases. A read-only preflight verified the exact cluster identity, 174-database baseline, candidate-name absence, and three protected databases. Before pytest, ownership was `pin_test`, the two fixtures were empty, and the only catalog delta was +2 (176 total). The assigned cluster identity was `pin_test` on `127.0.0.1:51017`, PostgreSQL 18.6 / version 180006, data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`.

The executed command was:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_retention_restore_postgres.py --junitxml C:\Users\roman\AppData\Local\Temp\pin_issue23_restore_20261016f7ad49b1\focused-junit.xml --tb=short
```

The test set `PIN_DATABASE_URL` explicitly to the source for the Alembic upgrade and to the target for each replay CLI subprocess. It did not load `.env` or `.env.local` for database credentials. `pg_dump.exe` and `pg_restore.exe` were invoked only from `D:/Program/postgresql/bin`; both reported PostgreSQL 18.6 and the checked dump/restore calls returned 0. The custom-format archive was 138,605 bytes, SHA-256 `5EAADDAC47BFC6FAD0C4F9538F8489F0E34ECBDA21C3B0C47FD653D6359F9B66`.

**Observed result:** JUnit reports 1 test, 0 failures, 0 errors, 0 skipped (53.929 seconds suite time; testcase 41.727 seconds). Missing and malformed deletion artifacts each failed closed; the valid tombstone replay completed twice with one replayed tombstone each time. The restored snapshot contained the pre-deletion plan and one `started` delivery attempt. After replay, the deleted owner's plan was absent, its retained trial activation, paid-period row, and retained billing identity remained, and the unrelated owner's plan remained. The actual delivery worker changed the restored old attempt to `unknown` without calling the fake transport again and without creating another attempt.

After the test completed, a read-only ownership/session check found both manifest databases owned by `pin_test` with zero sessions. The outer manifest dropped only those exact names. An independent post-cleanup catalog query matched the captured pre-create database/owner list exactly (174 non-template databases); both fixture names were absent and all three protected names remained present.

## Raw packet and checks

The preserved packet is `C:/Users/roman/AppData/Local/Temp/pin_issue23_restore_20261016f7ad49b1/`. It contains the exact command, console log, JUnit XML, pre/post catalog captures, creation/drop logs, manifest, preserved synthetic snapshot/ledger, artifact hashes/versions, and cleanup receipts:

- `focused-command.txt`, `focused-pytest.log`, `focused-junit.xml`
- `precreate_catalog_verified.txt`, `postcreate_catalog.txt`, `precleanup_ownership_connections.txt`, `postcleanup_independent_catalog.txt`
- `manifest.json`, `manifest_create.sql`, `manifest_create.log`, `manifest_drop.log`, `environment-keys.txt`, `attempt-and-cleanup-notes.txt`
- `snapshot.dump`, `deletions.jsonl`, `artifact-receipt.txt`

This run used checkout HEAD `5ede0c40965cf4c4ab10d0a94d88d169efb15fb6`, with product source unchanged. Test source SHA-256: `A74373175D7C74F1245B9626A5AA8E3CEDE75F3B6E0526A299B0CA2030BE0E87`. Static checks passed on that file: Ruff, targeted mypy, and `py_compile`. There was one read-only guard-command formatting stop before any database mutation: PostgreSQL rendered the server address with `/32`; the guard was corrected to use `host(inet_server_addr())`, after which the exact identity check passed. The initial non-mutating preflight output is also retained in the packet.

Root direct verification:reviewed the test before native execution and matched its unchanged SHA-256 afterward; verified raw1-case JUnit (0failures/errors/skips,53.929s), preserved138605-byte dump hash, identical pre/post catalog captures, manifest/ownership/session and pgtool receipts. Root independently queried the live cluster and matched all174pre-create names/owners, protected3presence and both fixture absences; independent Ruff/targetedmypy passed. This is separate focused evidence afterbe53f92PASS557, not a new whole-suite total. The unknown-send assertion covers only an attempt already persisted in the snapshot.
