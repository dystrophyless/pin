# Billing voice reservation extension — local evidence

Date: 2026-10-14. Source: `39ee65a` (local, unpushed). This is a shared prerequisite for voice issue [#18](https://github.com/dystrophyless/pin/issues/18), extending accepted Billing #6; it does not accept the complete voice feature.

`ResizeVoiceUsage` changes only a live positive voice hold, preserving its original period, AI units and expiry. Quota checks exclude the reservation's old hold. Missing, terminal, expired, AI-only, deleted-account and over-quota requests fail without changing the hold. Account/period/reservation locks serialize concurrent increases and new reservations. The owner-scoped getter reads existing reservations without creating or moving them.

Root verification used `git archive` of the exact source in `C:/Users/roman/AppData/Local/Temp/pin-billing-resize-proof-20261014-39ee/source`, with that source's `src` first on `PYTHONPATH`. Environment: Windows, project Python 3.12, locked dependencies and PostgreSQL 18.6. Fixture `pin_issue6_resize_proof_20261014_39ee` was created only after checking the owned cluster at `127.0.0.1:51017` and exact data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`.

- `python -m pytest tests/test_billing_access.py tests/test_billing_access_postgres.py -q`: **43 passed**, zero skips, 9.49 seconds; JUnit and command/source/environment provenance saved in the packet.
- Exact-source Alembic check, downgrade to base, upgrade to head, check: **PASS**, zero drift. This archived source's head is `conversation_capture_0001`; in-flight feature migrations were excluded.
- Worker scoped Ruff/mypy and root `git diff --check`: **PASS**. Root also independently ran the 43 tests against the integration tree before committing.
- Proof fixture and completed helper fixture `pin_issue6_voice_resize_worker_20261014_d91f` were removed after cluster/name checks; their absence was verified. The historical model receipt database and active feature fixtures were preserved.

The local packet contains `source.zip`, `provenance.json`, `checks.log`, `pytest.xml`, `migrations.log` and `cleanup.json`. No external model/payment calls, deployment, Docker or push occurred. Speech decoding, actual recognition, prepared Conversation continuation and full issue #18 integration need their own evidence.
