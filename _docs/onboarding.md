# Preferences onboarding runtime

Private Telegram text updates enter through the trusted owner resolver and durable inbox. The application worker claims each update through the application lane, checks the inbox owner against the fenced claim, then runs Identity and the published Billing trial API in one SQLAlchemy session. Profile confirmation, trial activation, the durable job receipt, and the render request commit or roll back together.

The first-run flow asks for a language (`ru` or `en`), an explicitly confirmed IANA home time zone, reminder offsets, and quiet/review times. `suggested` (shown as localized quick replies) selects one calendar day and one elapsed hour for reminders, plus quiet hours `22:00–08:00` and review time `20:00`. The `Asia/Almaty` example button applies only after an explicit tap; the bot does not infer a zone from language or location. The final summary requires an explicit localized confirmation; `/cancel` removes the draft without saving preferences or starting a trial. Each step is stored and resumes after a worker restart.

`/settings` opens a versioned preference preview. `keep` preserves the value for the current step. Preview cancellation leaves the profile and trial untouched. Confirmation uses optimistic profile versions; a stale preview is discarded and the user is asked to reopen `/settings`. Quick replies use opaque, expiring, owner-bound, single-use callback tokens. Their server-side binding includes the active draft UUID and version, preventing an unused button from applying to a new draft after cancellation and restart. A confirmed home-zone change emits `identity.home_zone_change.requested`, including the prior zone, requested zone, and preference version. This is a handoff for future plan-mutation work; the current worker does not shift saved plans.

Replies are appended as `telegram.reply.requested` outbox events with the private chat ID, MarkdownV2 and plain-text renderings, handling disposition, and source job ID. The application worker does not send to Telegram. Ordinary free text outside onboarding/settings returns `unhandled` and explicitly says the message was not saved. Plan capture, AI fallback, and production delivery outcomes remain later work.

Trial creation and retained-account return behavior belong to Billing. Confirmation can activate one trial; replay and later `/start` do not reset preferences or trial history. The Billing lifecycle marker records only the identity facts needed to prevent a repeat trial. No retention duration or deleted-plan restoration is implied here.

## Local verification

Run the onboarding PostgreSQL tests against an owned disposable PostgreSQL database with the schema upgraded to Alembic `head`:

```powershell
$env:PIN_TEST_ONBOARDING_DATABASE_URL = 'postgresql+psycopg://pin_test@127.0.0.1:51017/<owned-test-database>'
uv run pytest -q tests/test_identity_onboarding_postgres.py
Remove-Item Env:PIN_TEST_ONBOARDING_DATABASE_URL
```

The integration suite covers the real Telegram owner resolver and durable inbox/job path, restart-safe steps, defaults, confirmation with the Billing API, replay and return behavior, cancellation, settings preview/cancel, stale-version conflict, zone-change handoff, explicit unsupported-plan disposition, same-transaction rollback after Billing writes, invalid zone/offset/time inputs, forged/foreign/stale/duplicate callbacks, same-version cancel/recreate protection, and concurrent confirmation.
