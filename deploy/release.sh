#!/bin/sh
set -eu

release_id=${1:?usage: release.sh RELEASE_ID}
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

# Keep a pre-migration database and deletion-ledger backup, then quiesce all workers.
compose up -d --wait postgres
if [ -L /opt/pin/current ]; then
  previous_release=$(readlink -f /opt/pin/current)
  case "$previous_release" in
    /opt/pin/releases/*) ;;
    *) echo "current release symlink is outside /opt/pin/releases" >&2; exit 1 ;;
  esac
  /bin/sh "$previous_release/deploy/backup.sh" "$previous_release" true
fi
compose stop -t 60 gateway app-worker delivery-worker speech-worker retention

# Migrate and restore deletion fences before restarting any worker.
compose --profile ops run --rm migrate
compose --profile ops run --rm retention pin-retention replay \
  --artifact /var/lib/pin-retention/deletion-ledger.jsonl
# A restored database stays fenced until its reconciliation receipts are recorded.
compose --profile ops run --rm operations pin-recovery check
compose up -d --wait --wait-timeout 120

# Compose waits for the database healthcheck, while the operational snapshot
# verifies that all four long-running worker roles have begun heartbeating.
umask 077
readiness_dir=$(mktemp -d /tmp/pin-readiness.XXXXXX)
trap 'rm -rf -- "$readiness_dir"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
ready=false
attempt=1
while [ "$attempt" -le 60 ]; do
  if compose --profile ops run --rm operations > "$readiness_dir/operations.json" \
    && compose ps --all --format json > "$readiness_dir/containers.json" \
    && python3 "$release_dir/deploy/startup_readiness.py" \
      "$readiness_dir/operations.json" "$readiness_dir/containers.json" \
      > "$readiness_dir/result.txt" 2>&1; then
    ready=true
    break
  fi
  sleep 2
  attempt=$((attempt + 1))
done
if [ "$ready" != true ]; then
  cat "$readiness_dir/result.txt" 2>/dev/null || true
  echo "runtime workers did not become ready after 60 checks" >&2
  exit 1
fi
rm -rf -- "$readiness_dir"
trap - EXIT HUP INT TERM

ln -sfn "$release_dir" /opt/pin/current.next
mv -Tf /opt/pin/current.next /opt/pin/current
