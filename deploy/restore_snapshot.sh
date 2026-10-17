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

restore_hosted() {
  runtime_env_file=$(realpath -e "$PIN_RUNTIME_ENV_FILE")
  [ -f "$runtime_env_file" ] && [ ! -L "$PIN_RUNTIME_ENV_FILE" ] || {
    echo "PIN_RUNTIME_ENV_FILE must identify a private regular runtime environment file" >&2
    exit 2
  }
  release_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
  release_id=${release_dir##*/}
  case "$release_id" in
    *[!a-f0-9]*|'') echo "restore must run from a pinned release directory" >&2; exit 2 ;;
  esac
  compose_file="$release_dir/compose.yaml"
  image="pin:$release_id"
  [ -f "$compose_file" ] || {
    echo "pinned release compose.yaml is missing" >&2
    exit 2
  }
  command -v docker >/dev/null 2>&1 || {
    echo "Docker is required for hosted restore" >&2
    exit 2
  }

  target_db=$(python3 - <<'PY'
import os
import re
from urllib.parse import urlsplit

try:
    url = urlsplit(os.environ["PIN_DATABASE_URL"])
    name = url.path.removeprefix("/")
    port = url.port
except (KeyError, ValueError):
    raise SystemExit("PIN_DATABASE_URL must be a valid private PostgreSQL URL") from None
if (
    url.scheme != "postgresql+psycopg"
    or url.hostname != "postgres"
    or port not in (None, 5432)
    or url.username != "pin"
    or not url.password
    or not re.fullmatch(r"pin_recovery_[a-z0-9_]+", name)
    or url.query
    or url.fragment
):
    raise SystemExit(
        "PIN_DATABASE_URL must target postgres:5432 as pin in a pin_recovery_* database"
    )
print(name)
PY
)

  compose() {
    env PIN_IMAGE="$image" docker compose \
      --env-file "$runtime_env_file" --file "$compose_file" "$@"
  }
  postgres_id=$(compose ps -q postgres)
  [ -n "$postgres_id" ] || {
    echo "the pinned PostgreSQL service must already be running" >&2
    exit 2
  }
  postgres_image=$(docker inspect --format '{{.Config.Image}} {{.State.Running}}' "$postgres_id")
  [ "$postgres_image" = "postgres:18.6-bookworm true" ] || {
    echo "the running PostgreSQL service is not the pinned 18.6 image" >&2
    exit 2
  }
  app_image_id=$(docker image inspect --format '{{.Id}}' "$image") || {
    echo "the pinned release image is not already present locally" >&2
    exit 2
  }
  [ -n "$app_image_id" ] || {
    echo "the pinned release image is not already present locally" >&2
    exit 2
  }

  temporary=$(mktemp -d)
  trap 'rm -rf "$temporary"' EXIT HUP INT TERM
  chmod 700 "$temporary"
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
  chmod 600 "$temporary/deletion-ledger.jsonl"

  database_identity=$(compose exec -T postgres psql -XAt -U pin -d "$target_db" \
    -c 'SELECT current_database()')
  [ "$database_identity" = "$target_db" ] || {
    echo "connected database identity does not match the explicit target" >&2
    exit 2
  }
  database_owner=$(compose exec -T postgres psql -XAt -U pin -d "$target_db" \
    -c 'SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()')
  empty=$(compose exec -T postgres psql -XAt -U pin -d "$target_db" \
    -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','p','v','m','S','f')")
  custom_schemas=$(compose exec -T postgres psql -XAt -U pin -d "$target_db" \
    -c "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','public') AND nspname NOT LIKE 'pg_%'")
  [ "$database_owner" = pin ] && [ "$empty" = 0 ] && [ "$custom_schemas" = 0 ] || {
    echo "target recovery database must be empty and owned by pin" >&2
    exit 2
  }

  compose exec -T postgres pg_restore --list < "$snapshot/database.dump" >/dev/null
  compose exec -T postgres pg_restore --no-owner --no-privileges --exit-on-error \
    --single-transaction --username=pin --dbname="$target_db" < "$snapshot/database.dump"
  PIN_DATABASE_URL=$database_url compose --profile ops run --rm --no-deps --pull never \
    -e PIN_DATABASE_URL migrate
  PIN_DATABASE_URL=$database_url compose --profile ops run --rm --no-deps --pull never \
    -e PIN_DATABASE_URL --entrypoint pin-recovery operations arm \
    --snapshot-id "$snapshot_id" --ledger-id "$ledger_id"
  PIN_DATABASE_URL=$database_url compose --profile ops run --rm --no-deps --pull never \
    -e PIN_DATABASE_URL --user "$(id -u):$(id -g)" \
    --volume "$temporary:/run/pin-recovery:ro" \
    --entrypoint pin-retention retention replay \
    --artifact /run/pin-recovery/deletion-ledger.jsonl
  PIN_DATABASE_URL=$database_url compose --profile ops run --rm --no-deps --pull never \
    -e PIN_DATABASE_URL --entrypoint pin-recovery operations status
  echo "restore complete; runtime roles remain fenced pending deletion, delivery, payment, and usage reconciliation"
}

if [ -n "${PIN_RUNTIME_ENV_FILE:-}" ]; then
  restore_hosted
  exit
fi

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
