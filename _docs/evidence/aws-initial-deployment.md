# AWS initial deployment evidence — 2026-10-17

## Status

The staging deployment and backup workflow runs are green. The stack is running and has passed bounded service/API and Russian and English landing-page checks. The owner confirmed that the deployed bot answered `/start`. This does not establish the complete planning dialogue, capacity or public readiness. No real voice request has been verified on the deployed worker. Domain/HTTPS and full disaster recovery remain unverified.

## Host and access setup

- Lightsail: Ubuntu 24.04 x86_64, 4 GB RAM, 2 vCPU, 80 GB disk, Frankfurt, with static IPv4 attached. Base price is about **$24/month before tax**. Host and the initial small backup fit within the current **$25/month before-tax planning estimate**; this is not a hard billing cap, and Qwen/provider spend is separate. The existing Infopedia instance was left untouched.
- Docker Engine 29.1.3 and Compose 2.40.3 are installed. Security updates were applied and the host rebooted. Runtime secret configuration has restrictive file permissions; secret values are not recorded. Telegram `getMe` verified bot username `pin_appbot`.
- GitHub production deploy is restricted to `main`; OIDC trust is bound to the exact immutable Pin identity. Temporary runner resources and their associated SSH rule were removed after earlier failed attempts; the persistent Lightsail SSH-connect alias remains the only SSH ingress. Network and secret checks passed: only HTTP port 80 is publicly reachable, PostgreSQL is not published, and the root-owned runtime environment file is mode 0600 and validated. The ECDSA certificate and public-key sidecar were validated over live SSH.
- PostgreSQL is private. Daily S3 backup storage uses AES-256 encryption with a seven-day lifecycle configured. Actual expiry has not yet been observed.

## Deployment and runtime evidence

- [First deployment run](https://github.com/dystrophyless/pin/actions/runs/38983013389): green; all 13 local deploy-helper unit tests passed, and the Actions checks job was green. Triggered from revision `98ada40`, which contains the helper and workflow SSH-sidecar fix. The built image (1.38 GB) was loaded. All seven services started, including four workers; PostgreSQL reported healthy.
- An operations snapshot after the backup restart reported the database ready and all four roles running. Production bootstrap was initially enabled, then set to `false` by an API PATCH that returned HTTP 204.
- The hosted monitor was then verified by stopping each long-running lane in turn: gateway, application worker, delivery worker, and speech worker. Every stop produced the expected failed health check; after each worker was restored and its heartbeat returned, a new check passed. Following an unattended kernel-update reboot, all seven containers started automatically, PostgreSQL became healthy, the five-minute monitor timer remained active, and a manual health check passed. The persistent bootstrap marker remained present and no further reboot was pending. See [hosted monitor evidence](issue-22-host-monitor.md).
- Idle host memory after stack startup showed 2,353 MiB available. This is an idle snapshot taken before the one-off model-load smoke, not a capacity or load result.
- A temporary HTTP landing page by IP was authorized. Browser verification confirmed Russian and English translated headings and the bot CTA to `https://t.me/pin_appbot`. No domain is supplied, so HTTPS and public readiness remain unverified.
- [Backup workflow run](https://github.com/dystrophyless/pin/actions/runs/38983415786): green. S3 `HeadObject` verified the uploaded 19,580-byte AES-256 encrypted object. A separate `GetObject` download and archive checksum check passed for the database dump (135,172 bytes) and ledger archive (169 bytes). The seven-day lifecycle is configured, while future expiration has not yet been observed.

## Setup corrections and remaining verification

Earlier attempts exposed a Linux-incompatible `msvcrt` guard, an OIDC subject mismatch and an AWS response without optional `expiresAt`; these were corrected. The Docker stable-virtual-environment fix is in source revision `9269b74`; revision `98ada40` contains the deploy helper and workflow SSH-sidecar fix. These corrections are setup evidence, not proof of complete recovery or product acceptance.

A same-image one-off Whisper smoke loaded the small CPU-int8 model with one CPU thread in 6.98 seconds (peak RSS 773.8 MiB) and ran a synthetic 10-second silence input with VAD disabled in 5.23 seconds (peak RSS 941.7 MiB). This measures only that synthetic memory/load case; it is not real-voice quality, throughput or capacity evidence. The one-off process exited; weights are now cached, while the application worker still loads lazily on its first real voice job.

A checksum-matched host-side backup copy restored into an explicitly named disposable database: `pg_restore --exit-on-error` passed; all 52 tables across non-system schemas and migration `retention_deletion_001` were present. The disposable database was removed and the live database was unchanged.

A subsequent rehearsal downloaded the latest encrypted S3 object through CloudShell, checked both archive digests, and streamed its database dump over temporary authenticated SSH into a new disposable database on the existing host. The restore passed with 52 application tables; the snapshot was 4.59 hours old and elapsed download/restore time was 10.57 seconds. The target was removed; the live database was unchanged. These are bounded snapshot-age and restore-time observations, not whole-host disaster RPO/RTO or post-backup deletion/payment/usage/unknown-send reconciliation proof.

Remaining issue-24 evidence: full isolated recovery with post-backup deletion/unknown-send and payment/usage reconciliation, whole-host RPO/RTO and key recovery. Record actual AWS costs and credit boundaries. The temporary landing remains HTTP; no domain or HTTPS evidence is available. The model smoke is bounded RAM evidence only and is not a quality or capacity result. The owner has not selected an individual-entrepreneur or company form in Kazakhstan; no statutory retention period or commercial-readiness verdict is inferred.

The owner removed the formal 99%/400-case/model-host qualification and actual Telegram Stars transaction testing from required MVP acceptance. No model-quality PASS is claimed. Functional integration, operations and recovery criteria remain in force.
