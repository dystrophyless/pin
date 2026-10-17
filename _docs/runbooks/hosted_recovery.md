# Hosted database snapshot recovery

Use this procedure only after choosing a verified database backup and a distinct,
newer verified backup bundle that contains the deletion ledger. Daily S3 objects
are complete encrypted backup bundles; download both bundles through the approved
operator access path and preserve the downloaded files for audit. A newer timestamp
and a valid checksum establish which bytes were downloaded, not that the deletion
ledger covers every deletion after the older snapshot.

The restore command accepts only an empty PostgreSQL database named
`pin_recovery_<unique-suffix>`. It restores that database, applies migrations,
replays the newer deletion ledger, and arms the runtime fence. It never starts
workers. Do not point it at the live database or an existing recovery database.

```sh
export PIN_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/pin_recovery_20261017_a'
deploy/restore_snapshot.sh \
  /secure/backups/pin-20261016T120000Z \
  /secure/backups/pin-20261017T120000Z
```

The script checks both `.verified` markers, both `SHA256SUMS` files, the dump,
the PostgreSQL target name and identity, and target emptiness. It requires the
newer bundle ID to be later than the snapshot ID. It extracts only one bounded,
regular `deletion-ledger.jsonl` member from the newer ledger archive, then runs
the existing `pin-retention replay` command while the database is fenced. Inspect
the retention replay output and deletion results before continuing.

The fence blocks gateway, app, delivery, speech, billing-refund, regular release,
and scheduled retention work. Configuration checks, migrations, gate status, and
`pin-retention replay` remain available. Restored delivery work in the uncertain
window becomes a terminal `unknown` outcome instead of being sent again. The
conservative cutoff is the exact runtime fence time, not the backup directory
timestamp: the database dump can contain rows written after that directory name
was chosen while workers quiesce. Any possibly due delivery or reply created by
the fence, and any regenerated occurrence from a plan or series created by the
fence, is unknown when its due time is no later than the fence. This can also
classify older queued work as unknown, which is safer than resending a message
whose provider result cannot be recovered. A planning change with a new source
event after the fence remains eligible even when it changes an older plan. The
same persisted snapshot/fence times are retained after release so any later
discovered old queued item still takes the unknown path. Unknown outcomes are
terminal and do not trigger a check-in follow-up timer.

Before releasing the gate, reconcile the entire interval from the snapshot time
through the fence time against independently retained sources:

- Verify complete tombstone coverage in the newer deletion ledger and check the
  replay outcome. A newer bundle timestamp alone is insufficient.
- Reconcile reminder sends and private replies against provider delivery records;
  classify every possibly sent but unconfirmed item as `unknown`.
- Reconcile payments, refunds, and usage/entitlement records against their
  authoritative provider and internal records. Resolve discrepancies without
  issuing new paid calls or repeating refunds.
- Save each source export/report as a local evidence file. Its receipt records
  the absolute path and SHA-256 digest so the exact reviewed bytes can be checked.

Create four operator-reviewed JSON receipts, one for each category. Use the same
snapshot and exact recovery window in every receipt. For example, the shape is:

```json
{
  "status": "operator_verified",
  "checked_at": "<UTC timestamp after the fence was armed>",
  "snapshot_id": "pin-20261016T120000Z",
  "window": {
    "start": "2026-10-16T12:00:00Z",
    "end": "<the exact fenced_at UTC timestamp from pin-recovery status>"
  },
  "reconciled": true,
  "sources": [
    {
      "path": "<absolute path to an actually reviewed evidence file>",
      "sha256": "<sha256 of that exact evidence file>"
    }
  ]
}
```

The deletion receipt must also set `ledger_id` to the newer bundle ID and
`tombstone_coverage` to `complete`. Do not write these receipts until the checks
are complete. The CLI verifies receipt structure, exact interval, source-file
digests, and deletion-ledger identity/coverage fields. It cannot query providers,
prove the source exports are complete, or prove the operator's conclusions; the
operator must review the evidence and sign off honestly. Missing or uncertain
evidence means leave the gate fenced.

```sh
PIN_DATABASE_URL="$PIN_DATABASE_URL" pin-recovery release \
  --deletion-evidence /secure/recovery/deletion.json \
  --delivery-evidence /secure/recovery/delivery.json \
  --payment-evidence /secure/recovery/payment.json \
  --usage-evidence /secure/recovery/usage.json
PIN_DATABASE_URL="$PIN_DATABASE_URL" pin-recovery check
```

Only after `check` succeeds may the operator use the normal deployment procedure
for this recovery database. `deploy/release.sh` independently checks the fence
before starting services. Keep the source bundles, evidence files, gate status,
and replay output together under the incident record. Preserve the existing
seven-day backup retention policy; this procedure does not extend it.
