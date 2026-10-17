#!/bin/sh
set -eu

backup_dir=${PIN_BACKUP_DIR:-/var/backups/pin}
valid_backup_name() {
  case "$1" in
    pin-????????T??????Z)
      timestamp=${1#pin-}
      case "$timestamp" in *[!0-9TZ]*) return 1 ;; esac
      return 0
      ;;
    *) return 1 ;;
  esac
}

prune_old_verified_backups() {
  root=$(realpath -e "$1")
  [ "$root" != / ] || return 1
  latest=
  for candidate in "$root"/pin-????????T??????Z; do
    [ -d "$candidate" ] && [ ! -L "$candidate" ] || continue
    name=${candidate##*/}
    valid_backup_name "$name" || continue
    [ -f "$candidate/.verified" ] && [ ! -L "$candidate/.verified" ] || continue
    latest=$candidate
  done
  [ -n "$latest" ] || return 0

  for candidate in "$root"/pin-????????T??????Z; do
    [ -d "$candidate" ] && [ ! -L "$candidate" ] || continue
    name=${candidate##*/}
    valid_backup_name "$name" || continue
    [ -f "$candidate/.verified" ] && [ ! -L "$candidate/.verified" ] || continue
    [ "$candidate" != "$latest" ] || continue
    if find "$candidate" -maxdepth 0 -type d -mtime +7 -print -quit | grep -q .; then
      rm -rf -- "$candidate"
    fi
  done
}

verify_new_backup_bundle() {
  directory=$1
  python3 - "$directory" <<'PY'
import json
import os
import re
import sys
from datetime import datetime

directory = sys.argv[1]
name = os.path.basename(directory)
metadata_path = os.path.join(directory, "backup-metadata.json")
checksums_path = os.path.join(directory, "SHA256SUMS")
if any(not os.path.isfile(os.path.join(directory, item)) or os.path.islink(os.path.join(directory, item))
       for item in ("backup-metadata.json", "database.dump", "deletion-ledger.tar.gz", "SHA256SUMS")):
    raise SystemExit("new backup must contain regular metadata, payload, and checksum files")
try:
    with open(metadata_path, encoding="utf-8") as stream:
        metadata = json.load(stream)
except (OSError, json.JSONDecodeError):
    raise SystemExit("new backup metadata is missing or invalid") from None
if (not isinstance(metadata, dict)
        or set(metadata) != {"format_version", "backup_id", "started_at"}
        or type(metadata["format_version"]) is not int
        or metadata["format_version"] != 1):
    raise SystemExit("new backup metadata format is unsupported")
backup_id = metadata["backup_id"]
started_at = metadata["started_at"]
if not isinstance(backup_id, str) or not re.fullmatch(r"pin-\d{8}T\d{6}Z", backup_id):
    raise SystemExit("new backup metadata ID is invalid")
try:
    timestamp = datetime.strptime(backup_id, "pin-%Y%m%dT%H%M%SZ")
except ValueError:
    raise SystemExit("new backup metadata ID is invalid") from None
if name != backup_id or started_at != timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"):
    raise SystemExit("new backup metadata does not match its directory ID and start time")
with open(checksums_path, encoding="ascii") as stream:
    lines = stream.read().splitlines()
entries = [re.fullmatch(r"([0-9a-f]{64}) [ *]([A-Za-z0-9._-]+)", line) for line in lines]
expected = {"backup-metadata.json", "database.dump", "deletion-ledger.tar.gz"}
if any(entry is None for entry in entries) or {entry[2] for entry in entries if entry} != expected or len(entries) != len(expected):
    raise SystemExit("new backup checksum manifest must cover metadata and both payloads exactly")
PY
  (cd "$directory" && sha256sum --check --status SHA256SUMS)
}

mark_verified_backup() {
  requested=$1
  root=$(realpath -e "$backup_dir")
  [ -d "$requested" ] && [ ! -L "$requested" ] || {
    echo "refusing to mark a missing or linked backup directory" >&2
    return 1
  }
  destination=$(realpath -e "$requested")
  [ "$(dirname "$destination")" = "$root" ] && valid_backup_name "${destination##*/}" || {
    echo "refusing to mark a path outside the owned backup directory" >&2
    return 1
  }
  verify_new_backup_bundle "$destination"
  umask 077
  printf 'verified %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$destination/.verified.tmp"
  mv -f "$destination/.verified.tmp" "$destination/.verified"
  chmod 600 "$destination/.verified"
  prune_old_verified_backups "$root"
}

if [ "${1:-}" = --mark-verified ]; then
  [ "$#" -eq 2 ] || {
    echo "usage: backup.sh --mark-verified /var/backups/pin/pin-TIMESTAMPZ" >&2
    exit 2
  }
  mark_verified_backup "$2"
  exit
fi

release_dir=${1:-/opt/pin/current}
keep_workers_stopped=${2:-false}
if [ "$keep_workers_stopped" != false ] && [ "$keep_workers_stopped" != true ]; then
  echo "usage: backup.sh [RELEASE_DIR] [true|false]" >&2
  exit 2
fi
env_file=${PIN_RUNTIME_ENV_FILE:-/opt/pin/shared/runtime.env}
release_dir=$(readlink -f "$release_dir")
release_id=$(basename "$release_dir")
backup_clock=$(date -u +'%Y%m%dT%H%M%SZ %Y-%m-%dT%H:%M:%SZ')
set -- $backup_clock
timestamp=$1
started_at=$2
destination="$backup_dir/pin-$timestamp"
temporary="$backup_dir/.pin-$timestamp.tmp"

umask 077
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
mkdir -m 700 "$temporary"
stopped=false

compose() {
  env PIN_IMAGE="pin:$release_id" docker compose \
    --env-file "$env_file" --file "$release_dir/compose.yaml" "$@"
}

compose --profile ops run --rm --no-deps retention sh -ec \
  'test -f /var/lib/pin-retention/deletion-ledger.jsonl'

finish() {
  result=$?
  trap - EXIT
  if [ "$stopped" = true ] && {
    [ "$keep_workers_stopped" != true ] || [ "$result" -ne 0 ];
  }; then
    compose up -d --wait --wait-timeout 120 || result=$?
    compose --profile ops run --rm operations || result=$?
  fi
  rm -f "$temporary/database.dump" "$temporary/deletion-ledger.tar.gz" \
    "$temporary/backup-metadata.json" "$temporary/SHA256SUMS"
  rmdir "$temporary" 2>/dev/null || true
  exit "$result"
}
trap finish EXIT

if [ -e "$destination" ]; then
  echo "backup destination already exists" >&2
  exit 1
fi

# Quiesce all ledger writers so the database dump and replay artifact form one restore point.
stopped=true
compose stop -t 60 gateway app-worker delivery-worker speech-worker retention
compose exec -T postgres sh -ec \
  'pg_dump --format=custom --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  > "$temporary/database.dump"
compose --profile ops run --rm --no-deps retention sh -ec \
  'tar -czf - -C /var/lib/pin-retention .' > "$temporary/deletion-ledger.tar.gz"
test -s "$temporary/database.dump"
test -s "$temporary/deletion-ledger.tar.gz"
printf '{"format_version":1,"backup_id":"pin-%s","started_at":"%s"}\n' \
  "$timestamp" "$started_at" > "$temporary/backup-metadata.json"
(cd "$temporary" && sha256sum backup-metadata.json database.dump deletion-ledger.tar.gz > SHA256SUMS)
chmod 600 "$temporary/backup-metadata.json" "$temporary/database.dump" \
  "$temporary/deletion-ledger.tar.gz" "$temporary/SHA256SUMS"
mv "$temporary" "$destination"
(cd "$destination" && sha256sum --check --status SHA256SUMS)
umask 077
printf 'verified %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$destination/.verified"
chmod 600 "$destination/.verified"
prune_old_verified_backups "$backup_dir"
if [ "$keep_workers_stopped" != true ]; then
  compose up -d --wait --wait-timeout 120
  compose --profile ops run --rm operations
fi
stopped=false
printf 'backup=%s\n' "$destination"
