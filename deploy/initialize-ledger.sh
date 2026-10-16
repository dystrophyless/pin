#!/bin/sh
set -eu

release_id=${1:?usage: initialize-ledger.sh RELEASE_ID --verified-empty-database}
confirmation=${2:-}
if [ "$confirmation" != --verified-empty-database ]; then
  echo "refusing ledger initialization without --verified-empty-database" >&2
  exit 2
fi
case "$release_id" in
  *[!a-f0-9]*|'') echo "release id must be a lowercase git SHA" >&2; exit 2 ;;
esac

marker=/opt/pin/shared/initial-ledger-created
if [ -f "$marker" ]; then
  echo "initial deletion ledger already recorded"
  exit 0
fi
if [ -e /opt/pin/current ] || [ -L /opt/pin/current ]; then
  echo "refusing bootstrap because a current release already exists" >&2
  exit 1
fi

release_dir="/opt/pin/releases/$release_id"
env_file=/opt/pin/shared/runtime.env
image="pin:$release_id"
test -f "$release_dir/compose.yaml"
test -r "$env_file"
docker image inspect "$image" >/dev/null
compose() {
  env PIN_IMAGE="$image" docker compose \
    --env-file "$env_file" \
    --file "$release_dir/compose.yaml" \
    "$@"
}

compose up -d --wait postgres
assert_empty_database() {
  compose exec -T postgres sh -ec \
    'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --set=ON_ERROR_STOP=1' \
    < "$release_dir/deploy/assert-empty-database.sql" >/dev/null
}
assert_empty_database
compose --profile ops run --rm migrate
assert_empty_database
compose --profile ops run --rm retention pin-retention run \
  --artifact /var/lib/pin-retention/deletion-ledger.jsonl
compose --profile ops run --rm operations
printf '%s\n' "$release_id" > "$marker"
chmod 600 "$marker"
