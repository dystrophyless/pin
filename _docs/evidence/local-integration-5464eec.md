# Combined Windows local qualification

Qualified source: `5464eec0f958c7bf40febf3dfe77ba0e5ed50fe5`, tree `b54f439e074e6d712bc9f28367a8b27fd90b4808`. Git archive SHA-256: `99ecdeb40882fe7225d70191f82cf4c0a815c7f29dd76c80e752c1be8f300553`; 438 tracked files, individually hashed and rechecked. Untracked mascot output was excluded.

Raw local packet: `C:/Users/roman/AppData/Local/Temp/pin-combined-retention-qual-5464eec-20261015`. It contains README, qualification receipt, source inventory, commands, raw logs and JUnit. Root directly inspected JUnit and cleanup audit.

| Check | Observed result |
| --- | --- |
| Full pytest, all 33 native DB opt-ins configured | 543 passed; zero failures/errors/skips; 348.20 seconds |
| Ruff / mypy / architecture | PASS; 219 source files typed |
| Migration head/current/check/base/head/check | PASS; retention_deletion_0001; zero drift |
| Landing | Six tests and JavaScript syntax PASS; blank public destinations correctly block publication |
| Actual cached offline Whisper | RU/EN recognized; silence rejected |
| 50-owner local outer-loop dispatch-start p95 | 3.146 seconds, threshold 10 seconds; injected transport |
| Fixture cleanup | Exactly 33 manifest DBs removed; all absent; protected model and two sandbox DBs preserved |

Two pytest-asyncio deprecation warnings remain. No live Telegram, model-provider, financial, Docker or deployment calls occurred. Previous failing packets remain preserved; this passing source supersedes their local regression findings, not their historical observations.

This establishes the combined local regression baseline for implemented components, including deletion, pretrial onboarding, outbox lifecycle lock ordering and safe static post-deletion denial. It does not establish real payment/renewal/refund, actual model quality, target-host capacity, hosted alerts, encrypted off-host restore, financial retention compliance, public landing destinations or overall MVP acceptance. Issues 20–28 retain their unmet external acceptance conditions.
