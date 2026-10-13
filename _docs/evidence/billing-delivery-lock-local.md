# Billing delivery authorization lock — local evidence

Source: `4a555809de557726d29bd7f2362460910acf6161`, exported by `git archive` to an isolated directory. Windows local development, CPython 3.12 and the owned disposable PostgreSQL 18.6 cluster at port 51017. No Docker, hosted environment or provider I/O.

The exact-source focused SQL run passed **2 tests, 15 deselected**, zero skips, in 6.86s. It proves account deletion and entitlement-row mutation block until the caller authorization transaction ends; the next transaction sees the new denial; natural expiry still denies at the end instant despite held locks; foreign/missing owners cannot gain access; no usage reservations are created. Billing Ruff and mypy (9 source files) passed on the matching working-tree Billing source before commit.

Packet: `C:\Users\roman\AppData\Local\Temp\pin-billing-lock-4a55580-poavvdh2` contains source, manifest and pytest log. The fresh snapshot database and the earlier working-tree lock-port fixture were both dropped after active-connection checks; their absence was verified. Historical model and other workers' fixtures were preserved.

This qualifies the additive Billing port only. Reminder/recovery wiring, final authorization races, complete regression/migration lifecycle, dispatch-start benchmarks and real Telegram delivery remain issue #12 acceptance work. No issue is closed by this focused check.

## Private reply lifecycle fence

The follow-on source `de83bc2c6da1b56c1c6deef8044ca95f490dd433` adds `lock_account_lifecycle` for onboarding/private replies without active-entitlement gating. An isolated git-archive source run of both Billing unit and PostgreSQL test files passed **34 tests, zero skips**, in 8.19s. The added third locking-port case independently holds only the lifecycle account fence before the competing deletion, and confirms the next lifecycle read returns deleted. Existing access/quota/trial and read-only lifecycle regressions pass as part of that run.

Packet: `C:\Users\roman\AppData\Local\Temp\pin-billing-lifecycle-de83bc2-p7spc7h6`. Its fresh owned snapshot database was dropped after verifying no active connections, and absence was verified. This is Billing boundary evidence; downstream send wiring and issue #12 acceptance still remain pending.
