#!/bin/sh
set -eu

snapshot=${1:?usage: restore_snapshot.sh VERIFIED_SNAPSHOT_DIR NEWER_VERIFIED_LEDGER_DIR}
ledger_bundle=${2:?usage: restore_snapshot.sh VERIFIED_SNAPSHOT_DIR NEWER_VERIFIED_LEDGER_DIR}
database_url=${PIN_DATABASE_URL:?set PIN_DATABASE_URL to a new disposable recovery database}
[ ! -L "$snapshot" ] && [ ! -L "$ledger_bundle" ] || {
  echo "backup directories must not be symbolic links" >&2
  exit 2
}

snapshot=$(realpath -e "$snapshot")
ledger_bundle=$(realpath -e "$ledger_bundle")
snapshot_id=${snapshot##*/}
ledger_id=${ledger_bundle##*/}
case "$snapshot_id:$ledger_id" in
  pin-????????T??????Z:pin-????????T??????Z) ;;
  *) echo "backup directories must use the pin-UTC-timestamp format" >&2; exit 2 ;;
esac
python3 - "$snapshot_id" "$ledger_id" <<'PY'
import re
import sys
from datetime import datetime

def timestamp(value: str) -> datetime:
    if not re.fullmatch(r"pin-\d{8}T\d{6}Z", value):
        raise SystemExit("backup directories must use the pin-UTC-timestamp format")
    try:
        return datetime.strptime(value, "pin-%Y%m%dT%H%M%SZ")
    except ValueError:
        raise SystemExit("backup directory timestamp is invalid") from None

if timestamp(sys.argv[2]) <= timestamp(sys.argv[1]):
    raise SystemExit("deletion ledger bundle must be newer than the selected database snapshot")
PY
[ -f "$snapshot/.verified" ] && [ ! -L "$snapshot/.verified" ]
[ -f "$ledger_bundle/.verified" ] && [ ! -L "$ledger_bundle/.verified" ]
(cd "$snapshot" && sha256sum --check --status SHA256SUMS)
(cd "$ledger_bundle" && sha256sum --check --status SHA256SUMS)
test -s "$snapshot/database.dump"
test -s "$ledger_bundle/deletion-ledger.tar.gz"
pg_restore --list "$snapshot/database.dump" >/dev/null

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
chmod 700 "$temporary"
target_db=$(python3 - "$temporary/pg.env" <<'PY'
import os
import shlex
import sys
from urllib.parse import parse_qs, unquote, urlsplit

url = urlsplit(os.environ["PIN_DATABASE_URL"])
if url.scheme != "postgresql+psycopg" or not url.hostname:
    raise SystemExit("PIN_DATABASE_URL must identify a PostgreSQL recovery target")
name = unquote(url.path.strip("/"))
if not name.startswith("pin_recovery_") or len(name) == len("pin_recovery_"):
    raise SystemExit("target database name must begin with pin_recovery_ and be disposable")
options = parse_qs(url.query)
if set(options) - {"sslmode"} or len(options.get("sslmode", [])) > 1 or url.fragment:
    raise SystemExit("PIN_DATABASE_URL may include only the sslmode query option")
values = {
    "PGHOST": url.hostname,
    "PGPORT": url.port or 5432,
    "PGUSER": unquote(url.username or ""),
    "PGPASSWORD": unquote(url.password or ""),
    "PGDATABASE": unquote(name),
}
if "sslmode" in options:
    values["PGSSLMODE"] = options["sslmode"][0]
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    for key, value in values.items():
        stream.write(f"export {key}={shlex.quote(str(value))}\n")
print(name)
PY
)
. "$temporary/pg.env"
[ "$(psql -XAtqc 'SELECT current_database()')" = "$target_db" ] || {
  echo "connected database identity does not match the explicit target" >&2
  exit 2
}
empty=$(psql -XAtqc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','p','v','m','S','f')")
custom_schemas=$(psql -XAtqc "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','public') AND nspname NOT LIKE 'pg_%'")
[ "$empty" = 0 ] && [ "$custom_schemas" = 0 ] || {
  echo "target recovery database is not empty" >&2
  exit 2
}

# This command writes only the explicitly named disposable database and never starts services.
pg_restore --no-owner --no-privileges --exit-on-error --single-transaction \
  --dbname="$target_db" "$snapshot/database.dump"
PIN_DATABASE_URL=$database_url alembic upgrade head
PIN_DATABASE_URL=$database_url pin-recovery arm \
  --snapshot-id "$snapshot_id" --ledger-id "$ledger_id"

python3 - "$ledger_bundle/deletion-ledger.tar.gz" "$temporary/deletion-ledger.jsonl" <<'PY'
import sys
import tarfile

archive_path, output_path = sys.argv[1:]
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    ledger = [member for member in members if member.name in {"deletion-ledger.jsonl", "./deletion-ledger.jsonl"}]
    if len(ledger) != 1 or not ledger[0].isfile() or ledger[0].size > 2 * 1024**3:
        raise SystemExit("newer backup must contain one bounded regular deletion ledger")
    source = archive.extractfile(ledger[0])
    if source is None:
        raise SystemExit("deletion ledger could not be read")
    with source, open(output_path, "xb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
PY
artifact="$temporary/deletion-ledger.jsonl"
test -f "$artifact" && [ ! -L "$artifact" ]
PIN_DATABASE_URL=$database_url pin-retention replay --artifact "$artifact"
PIN_DATABASE_URL=$database_url pin-recovery status
echo "restore complete; runtime roles remain fenced pending deletion, delivery, payment, and usage reconciliation"
