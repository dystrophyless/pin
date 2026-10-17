# Local recovery verification — 2026-10-17

This records focused verification of recovery source committed in `2f79c5f`, paid-lifecycle coverage in `c8430e7` and operations coverage in `95df95e`. It does not establish a deployed release or complete MVP acceptance.

## Observed results

- Root offline suite: **282 passed**, JUnit `pin-recovery-offline-final-20261017.xml`, 61.70 seconds. Native PostgreSQL files and the persistence/migration suites were excluded explicitly. Cached speech checks ran offline; no new model/provider calls were made.
- Root Ruff across source, tests, migrations and deployment helpers passed; mypy passed all 221 source files; architecture import boundaries passed.
- Focused recovery and executable shell smoke: **11 passed**. The smoke executes the restore script with synthetic credentials and stubbed PostgreSQL/CLI commands, checks private environment extraction and command ordering, and verifies that credentials do not appear in output. It is not a real shell-driven database restore.
- Native notification delivery: **39 passed**, zero failures/errors/skips. This run preceded the later conservative fence-time cutoff and repeated-recovery correction; those changes have separate focused and restore evidence below. Earlier failed harness runs remain preserved.
- Native paid lifecycle: **4 passed**, zero failures/errors/skips, 51.02 seconds. Includes trial warnings, quiet-hour deferral, warning deduplication, expired-access behavior, retained plans and reminder denial, plus existing synthetic paid lifecycle cases. Root independently checked JUnit, the test source digest and cleanup receipt. Actual Stars transactions are removed from acceptance; these are synthetic integration checks.
- Actual PostgreSQL dump/restore, existing started-attempt case: **1 passed**, 31.88 seconds.
- Actual PostgreSQL dump/restore, absent-attempt case: **1 passed**, 20.62 seconds. A dump was taken with no delivery attempt; a fake source send and account deletion occurred afterward. The newer deletion ledger was exported and replayed while the restored target remained fenced. Runtime startup and release with missing usage evidence were refused. Release with hashed synthetic observations succeeded. Deleted plans remained absent, retained trial/paid/billing identity facts survived, and the actual target delivery worker classified the lost-window reminder as unknown with zero additional transport sends.
- Native migration lifecycle: **1 passed**, covering upgrade, downgrade to base and re-upgrade. The deployment initialization SQL accepted the pristine migrated database, rejected a changed recovery evidence field with the expected error, and transaction rollback restored the pristine gate. Root independently checked JUnit, assertion results and the exact catalog/source-hash cleanup receipt.
- Native operations suite: **6 passed**, zero failures/errors/skips, 11.09 seconds. The added case confirms a real deletion preview/tombstone, injects purge failure, and checks that the pending deletion remains visible through redacted operations diagnostics. The first run's incomplete fixture failed to invoke the callback; its raw failure is preserved. The corrected fixture retains callback-hit and diagnostic assertions. Root independently checked passing JUnit and the source digest; the exact fixture was removed and catalog baseline restored.

The first absent-attempt fixture failed at the intentional recovery database-name guard. Only the test target allowlist was adjusted to admit its dedicated `pin_recovery_issue23_` prefix; the production guard remains intact. Raw failed results are retained. Every assigned fixture was checked for ownership and zero sessions before exact-name deletion; the complete 174 non-template database catalog returned to its baseline after each run. No unrelated database was reset or removed.

## Evidence boundaries

The first Linux CI run stopped before deployment: the new restore script was committed without its executable bit, producing a permission error in the shell smoke (281 other checks passed). The script's Git mode was corrected to executable with no content change; the failed run remains visible in Actions. A later successful deployment must be recorded separately.

Recovery receipts bind operator assertions to a snapshot, time window and hashed source observations. They do not independently prove provider completeness or the truth of an operator's conclusions. The local test observations are explicitly synthetic; no real payment reconciliation is claimed.

These results do not prove full-host disaster recovery, a complete production Telegram journey, legal financial-retention compliance, or a model quality/capacity target. The running AWS bot remains on the previous committed release until the new changes are verified, committed and deployed. The owner confirmed that the deployed bot answered `/start`.

Private raw packets are retained locally under the dated recovery, paid-lifecycle and retention-restore verification directories. Public issues should reference the results and limitations without publishing workstation paths or private system identifiers.
