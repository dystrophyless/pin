# Lightsail deployment operations

## First release

Use the repository's actual OIDC `sub_claim_prefix` from GitHub's repository OIDC settings API, followed by `:environment:production`, in the AWS role trust. New repositories can include immutable owner/repository IDs in this prefix; do not assume the older name-only subject format. Keep the production environment restricted to `main`.

Copy `.env.example` to `/opt/pin/shared/runtime.env`, fill the required secrets and destination values, and set its permissions to `0600`. Keep `COMPOSE_PROFILES=landing-http` while no domain is configured. The first main-branch deployment needs the production variable `LIGHTSAIL_INITIALIZE_EMPTY_DB=true`; the initializer still verifies that no application table contains rows both before and after migrations. Switch the variable to `false` after it succeeds. A present deletion-ledger marker is never recreated by a regular release.

The HTTP landing container publishes port 80 and serves the reviewed `landing/` files. The Lightsail firewall must allow public TCP 80. The landing keeps its preview and offer-availability wording until those product checks are accepted.

## Optional HTTPS landing

When the owner has a domain, point its DNS A record at the instance and configure TCP 80/443 plus UDP 443 in Lightsail. In the root-readable runtime environment, set `PIN_DOMAIN` to that hostname and replace `COMPOSE_PROFILES=landing-http` with `COMPOSE_PROFILES=landing-https`. Redeploy or restart the Compose project. Caddy obtains and renews a certificate automatically; its state stays on the `pin-caddy-data` volume. Do not activate both landing profiles together because both bind port 80.

## Encrypted off-host backups

The `Pin encrypted database backup` workflow runs daily and can also be dispatched on `main`. It uses GitHub OIDC, a Lightsail temporary SSH key/certificate, and a runner-specific TCP 22 rule that is removed and verified after the job. The backup is encrypted with S3-managed AES-256 and uploaded to `daily/` in the configured AWS region. The workflow never receives a permanent host key or S3 credential.

The bucket lifecycle expires `daily/` objects after 7 days and aborts incomplete multipart uploads after 1 day. The GitHub production environment requires `AWS_ROLE_ARN`, `AWS_REGION`, `LIGHTSAIL_INSTANCE_NAME`, and `PIN_BACKUP_BUCKET`. Add `s3:PutObject` on only `arn:aws:s3:::<bucket>/daily/*` to the OIDC role assumed by this workflow; keep Lightsail access and port mutations scoped to the Pin instance. `GetInstancePortStates` is read-only and can use `Resource: "*"` where Lightsail does not support resource-level authorization.

Each archive is rejected above 2 GiB. With daily runs and the configured 7-day lifecycle, steady-state S3 Standard storage is at most about 14 GiB before expiration lag; at Frankfurt's current first-tier rate this is under $1/month, plus small request charges. This size guard makes oversized backups fail before upload and needs an operator to revisit retention/cost settings as data grows. Keep bucket versioning disabled so expired backups do not leave billable noncurrent copies.

The host keeps checksum-verified local backup directories for up to 7 days and always retains the newest verified directory until a newer one passes its checksum check. Cleanup is restricted to immediate `pin-YYYYMMDDTHHMMSSZ` directories in `/var/backups/pin`; unmarked or unrecognized directories are left intact.

To restore, download the desired archive to a protected machine, extract it into a private directory, verify `SHA256SUMS`, and follow the project database restore and deletion-ledger replay runbook before starting workers. The workflow uploads backups but does not automate restore testing.

## Host health checks

The root-owned `pin-host-health.timer` runs every five minutes. It asks the current release's `pin-operations` container for the redacted database, queue, delivery, provider, speech, billing, retention and four-worker heartbeat snapshot; it also checks Compose service states and restart-count increases, and records host root-volume disk and Linux memory availability. Each run emits only filtered health fields and aggregate counts to the system journal; opaque IDs, payloads and provider errors are omitted. The one-shot service exits nonzero for runbook-actionable statuses, unavailable database/worker/service/resource checks, or a container restart-count increase. It also warns when root-volume free space is below 1 GiB or 10%, available memory is below 256 MiB, or oldest globally due pending work is older than 300 seconds. These are operator warning levels for this host check, not product availability targets. Queue age is global and does not identify a lane. Inspect the journal failure and follow the matching investigation steps in the [operations runbook](../_docs/runbooks/operations_local.md).

Install and run the monitor after the first successful release:

```sh
sudo install -o root -g root -m 0644 /opt/pin/current/deploy/pin-host-health.service /etc/systemd/system/
sudo install -o root -g root -m 0644 /opt/pin/current/deploy/pin-host-health.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pin-host-health.timer
sudo systemctl start pin-host-health.service
sudo journalctl -u pin-host-health.service --since '10 minutes ago' --no-pager
```

For a bounded end-to-end failure signal check, use a maintenance window and first confirm the selected worker owns no active lease or started external action. Stop only `app-worker`, run the one-shot service and expect its failure, then restore that worker from an `EXIT` trap. After it writes a fresh heartbeat, rerun the check and expect success. Do not use this probe during active work if pausing the worker could delay a task.

```sh
set -eu
release=$(readlink -f /opt/pin/current)
release_id=${release##*/}
compose() {
  sudo env PIN_IMAGE="pin:$release_id" docker compose \
    --env-file /opt/pin/shared/runtime.env \
    --file "$release/compose.yaml" "$@"
}
restore_app_worker() {
  compose up -d --wait --wait-timeout 120 app-worker
}
trap 'restore_app_worker' EXIT HUP INT TERM
compose stop -t 60 app-worker
if sudo systemctl start pin-host-health.service; then
  echo 'health check unexpectedly passed with app-worker stopped' >&2
  exit 1
fi
sudo journalctl -u pin-host-health.service --since '2 minutes ago' --no-pager
restore_app_worker
trap - EXIT HUP INT TERM
sleep 20
sudo systemctl reset-failed pin-host-health.service
sudo systemctl start pin-host-health.service
```
