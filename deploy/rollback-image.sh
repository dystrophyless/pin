#!/bin/sh
set -eu

release_id=${1:?usage: rollback-image.sh RELEASE_ID --schema-compatible}
confirmation=${2:-}
if [ "$confirmation" != --schema-compatible ]; then
  echo "refusing rollback without explicit --schema-compatible confirmation" >&2
  exit 2
fi
case "$release_id" in
  *[!a-f0-9]*|'') echo "release id must be a lowercase git SHA" >&2; exit 2 ;;
esac

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

compose run --rm retention pin-retention replay \
  --artifact /var/lib/pin-retention/deletion-ledger.jsonl
compose up -d --wait --wait-timeout 120
compose --profile ops run --rm operations
ln -sfn "$release_dir" /opt/pin/current.next
mv -Tf /opt/pin/current.next /opt/pin/current
