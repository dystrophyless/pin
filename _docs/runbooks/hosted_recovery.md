# Hosted database snapshot recovery

Use this procedure only after choosing a verified database backup and a distinct,
newer verified backup bundle that contains the deletion ledger. Daily S3 objects
are complete encrypted backup bundles; download both bundles through the approved
operator access path and preserve the downloaded files for audit. A newer timestamp
and a valid checksum establish which bytes were downloaded, not that the deletion
ledger covers every deletion after the older snapshot.

Each new bundle includes `backup-metadata.json` (format version 1) with the
original `backup_id` and UTC `started_at`; both fields are covered by
`SHA256SUMS`. The S3 object key timestamp is only the upload time and must never
be used as the database snapshot ID. The archive does not contain `.verified`:
the workflow excludes the host marker from the archive and also marks the host
copy after upload. For a downloaded
bundle, extract into a new private staging directory, verify the exact checksum
manifest, validate the metadata ID/time, and only then create a local marker:

```sh
umask 077
work=$(mktemp -d /secure/recovery-download.XXXXXX)
chmod 700 "$work"
mkdir -m 700 "$work/extracted"
python3 - /secure/downloads/backup.tar.gz "$work/extracted" <<'PY'
import sys, tarfile
from pathlib import Path

expected = {"backup-metadata.json", "database.dump", "deletion-ledger.tar.gz", "SHA256SUMS"}
destination = Path(sys.argv[2])
with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = archive.getmembers()
    files = {}
    for member in members:
        name = member.name.removeprefix("./").rstrip("/")
        if name in {"", "."} and member.isdir():
            continue
        if name not in expected or not member.isfile() or name in files:
            raise SystemExit("archive contains an unexpected or non-regular member")
        if member.size > 2 * 1024**3:
            raise SystemExit("archive member exceeds the 2 GiB bound")
        files[name] = member
    if set(files) != expected:
        raise SystemExit("archive does not contain the exact expected file set")
    for name, member in files.items():
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit("archive member could not be read")
        with source, (destination / name).open("xb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
PY
(cd "$work/extracted" && python3 - <<'PY'
import re
from pathlib import Path

lines = Path("SHA256SUMS").read_text(encoding="ascii").splitlines()
entries = [re.fullmatch(r"([0-9a-f]{64}) [ *]([A-Za-z0-9._-]+)", line) for line in lines]
expected = {"backup-metadata.json", "database.dump", "deletion-ledger.tar.gz"}
if any(entry is None for entry in entries) or {entry[2] for entry in entries if entry} != expected or len(entries) != len(expected):
    raise SystemExit("checksum manifest must cover metadata and both payloads exactly")
PY
sha256sum --check SHA256SUMS)
backup_id=$(python3 - "$work/extracted/backup-metadata.json" <<'PY'
import json, re, sys
from datetime import datetime
with open(sys.argv[1], encoding="utf-8") as stream:
    metadata = json.load(stream)
if (not isinstance(metadata, dict)
        or set(metadata) != {"format_version", "backup_id", "started_at"}
        or type(metadata["format_version"]) is not int
        or metadata["format_version"] != 1):
    raise SystemExit("unsupported backup metadata")
backup_id = metadata["backup_id"]
if not isinstance(backup_id, str) or not re.fullmatch(r"pin-\d{8}T\d{6}Z", backup_id):
    raise SystemExit("invalid backup ID")
stamp = datetime.strptime(backup_id, "pin-%Y%m%dT%H%M%SZ")
if not isinstance(metadata["started_at"], str) or metadata["started_at"] != stamp.strftime("%Y-%m-%dT%H:%M:%SZ"):
    raise SystemExit("backup start time does not match its ID")
print(backup_id)
PY
)
mv "$work/extracted" "/secure/backups/$backup_id"
printf 'verified locally after checksum and metadata checks %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "/secure/backups/$backup_id/.verified"
chmod 600 "/secure/backups/$backup_id/.verified"
```

The extraction step accepts only the four expected regular files
(`backup-metadata.json`, `database.dump`, `deletion-ledger.tar.gz`, and
`SHA256SUMS`) and writes their basename into a fresh private directory. The newer
ledger bundle is validated the same way. Keep the downloaded archive, extracted bundle, and
verification output in the incident record. Legacy archives without metadata
remain legacy: the automatic restore helper rejects them even when the folder or
S3 key looks like a `pin-...` timestamp. Never infer the original ID from the
upload key or renamed folder. Use a legacy archive only after independent
original-host provenance establishes its ID and checksum manifest, and perform
any migration into the supported metadata format as a separately recorded
manual step while preserving the original archive. If provenance is unavailable,
stop and leave recovery fenced.

Hosted restore runs from the currently selected pinned release and requires the
existing Compose `postgres` container to be running with image
`postgres:18.6-bookworm`, plus the exact `pin:<release-sha>` image already on the
host. The host needs Docker Compose, Python 3, `realpath`, and `sha256sum`; it
does not need the Pin virtual environment, `psql`, `pg_restore`, Alembic, or
`pin-retention`. PostgreSQL commands run inside the existing PostgreSQL
container. Migrations, recovery-gate commands, and ledger replay run as one-off
containers from the pinned Pin image. The helper uses `--no-deps` and
`--pull never`; it never starts runtime roles, builds or pulls images, or
creates/drops databases.

First create a new empty database owned by `pin` in the already-running local
PostgreSQL container. Choose a unique `pin_recovery_<suffix>` name and obtain
authorization for this temporary database before running `createdb`:

```sh
export PIN_RUNTIME_ENV_FILE=/opt/pin/shared/runtime.env
release_dir=$(readlink -f /opt/pin/current)
release_id=${release_dir##*/}
target_db=pin_recovery_20261017_a
PIN_IMAGE="pin:$release_id" docker compose \
  --env-file "$PIN_RUNTIME_ENV_FILE" --file "$release_dir/compose.yaml" \
  exec -T postgres createdb -U pin -O pin "$target_db"
```

Set `PIN_DATABASE_URL` from the protected host secret input, using the same
database password configured in `runtime.env`, host `postgres`, role `pin`, port
`5432`, and the exact target name. Enter it without putting the secret in shell
history or command arguments. For an interactive Bash session:

```sh
read -r -s -p 'Recovery database URL: ' PIN_DATABASE_URL
printf '\n'
export PIN_DATABASE_URL
/opt/pin/current/deploy/restore_snapshot.sh \
  /secure/backups/pin-20261016T120000Z \
  /secure/backups/pin-20261017T120000Z
unset PIN_DATABASE_URL
```

Setting `PIN_RUNTIME_ENV_FILE` explicitly selects hosted mode. The helper binds
to the currently selected release's `compose.yaml` and exact `pin:<release-sha>`
image, then verifies that the existing PostgreSQL container is running the
pinned image. It accepts only an already-created, empty target named
`pin_recovery_*`, owned by `pin`. It does not restore into `pin` or any existing
database with objects.

The script checks both `.verified` markers, both `SHA256SUMS` files, the dump,
the PostgreSQL target name and identity, and target emptiness. It requires the
newer bundle ID to be later than the snapshot ID. It extracts only one bounded,
regular `deletion-ledger.jsonl` member from the newer ledger archive, then runs
the existing `pin-retention replay` command while the database is fenced. Inspect
the retention replay output and deletion results before continuing. The one-off
replay runs as the invoking host UID/GID, matching the owner of its mode-600
temporary ledger file. Only this mode-700 scratch directory is mounted writable,
because the ledger reader creates and locks a private sidecar file. The original
backup bundles are not mounted into the replay container or modified.

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

After the recovery CLI reports the gate released, an authorized operator can
cut runtime over using the normal release procedure. Compose reads
`PIN_DATABASE_NAME` for the runtime URL, PostgreSQL initialization value, and
database health check; when unset it remains `pin`, preserving the production
default. Backup and initialization helpers use Compose's `POSTGRES_DB`, so
they follow the same selected database.

For a cutover, use a pinned release whose `compose.yaml` supports
`PIN_DATABASE_NAME`, set that variable in the protected
`/opt/pin/shared/runtime.env` to the exact validated recovery database name,
then invoke that release's normal script with its exact lowercase release SHA:

```sh
release_id=<release-sha>
/bin/sh "/opt/pin/releases/$release_id/deploy/release.sh" "$release_id"
```

The release will take its pre-release
backup, stop workers, migrate and replay retention against that selected
database, verify the released gate, and only then start workers. Do not run
this cutover during a rehearsal: keep the target isolated and workers stopped.
The procedure does not edit the runtime env file or perform the cutover itself.
Keep the source bundles, evidence files, gate status, and replay output together
under the incident record. Preserve the existing seven-day backup retention
policy; this procedure does not extend it.
