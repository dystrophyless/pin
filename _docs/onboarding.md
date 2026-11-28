# Telegram start and preferences

The application worker initializes preferences on the first valid private text update under the trusted owner and job fences. Its transaction also records the job receipt and the reply request. Returning users keep all saved preferences. An unfinished legacy initial draft is discarded when the default profile is created, and obsolete initial callbacks cannot resume setup or grant consent.

Telegram provides the sender's language tag. Pin uses Russian for the `ru` primary tag and English otherwise. Pin starts with UTC and shows the active zone on `/start`. The initial reminder offsets are one calendar day and one elapsed hour. Quiet hours run from 22:00 to 08:00, with evening review at 20:00 in the saved zone.

Users can open `/settings` when they want to edit preferences. The editor recognizes supported city names, such as London, New York, Moscow (Москва), and Almaty (Алматы), as well as IANA names. For an ambiguous or unrecognized city, the editor asks for a different input. Keep current preserves the saved value at each step. Cancellation leaves the profile unchanged. Confirmation uses optimistic profile versions. Settings callbacks are opaque, expiring, owner-bound and single-use. They include the draft UUID and version. Changing the home zone uses the existing schedule-impact preview and confirmation.

On first `/start`, on `/timezone`, and on the home-zone step of `/settings`, Pin can show a Telegram HTML Widget in the private chat. It displays the device's named time zone and sends only that zone and an expiring action token after a tap. The first-start and `/timezone` widget opens the normal confirmation preview with existing event schedules preserved. The settings widget fills the current draft and the remaining settings steps continue normally. Existing profiles and pending plans do not change before confirmation.

Webhook registrations that list specific update types must include `widget_query`. Local polling requests it explicitly. The widget needs no hosted page or location permission.

As of November 28, 2026, Telegram's Bot API maintainer reports that HTML Widgets cannot be enabled in production. Pin falls back to text if Telegram rejects a widget. `/timezone Almaty` and `/timezone Asia/Almaty` open a direct confirmation preview when the requested zone differs from the saved zone. Manually entered zones use the same validation and schedule protections as the device zone. [Telegram Bot API issue #947](https://github.com/tdlib/telegram-bot-api/issues/947#issuecomment-7132123307)

`/privacy` shows the disclosure and the explicit grant or withdrawal action. Granting consent activates an eligible one-time trial in the same transaction. AI work still requires an authorized grant at ingress and processing time. Users resend a request rejected for missing consent. Read the [processing consent contract](specifications/SPEC-001_processing_consent.md) for authorization and recovery details.

Planning reads the profile through Identity. Schedule validation uses `IdentityStore.lock_preferences(owner_id)` inside the caller's transaction to refresh and lock the profile. Telegram replies are appended as `telegram.reply.requested` outbox events with MarkdownV2, a plain-text fallback, and the source job ID.

## Local verification

Run the profile and settings integration tests against an owned disposable PostgreSQL database upgraded to Alembic `head`.

```powershell
$env:PIN_TEST_ONBOARDING_DATABASE_URL = 'postgresql+psycopg://pin_test@127.0.0.1:51017/<owned-test-database>'
uv run pytest -q tests/test_identity_onboarding_postgres.py
Remove-Item Env:PIN_TEST_ONBOARDING_DATABASE_URL
```

The tests cover language inference, defaults, retry and restart, obsolete setup callback rejection, explicit consent and one-trial activation, rollback, and versioned settings changes.

On 2026-11-28, the Windows working tree based on `593397c` passed the CI offline pytest selection (324 tests), the native Identity profile/settings module (18 tests), and the native core-lifecycle, payment/support, home-zone runtime, and consent integration modules (20 tests). The native runs used separate disposable databases on PostgreSQL 18.6. The test launcher set the expected port and data directory to the disposable cluster for the guarded modules, retaining their fixture identity checks. Ruff, mypy, architecture checks, and `git diff --check` passed.

The separate capture-runtime PostgreSQL module failed the same five cases on the working tree and the archived `593397c` source. That module's existing fixtures do not establish processing consent. Live Telegram and deployment were outside this verification.

The HTML Widget change passed the CI offline selection (335 tests), 19 native profile/settings tests, 13 native ingress tests, and 10 native home-zone runtime tests on the same PostgreSQL version. The new cases verify private webhook acknowledgement after durable commit and the widget-to-preview-to-save path, including invalid zones, foreign actors, and reused tokens. A local JavaScript harness verified the documented request/answer shape and the retry UI. Revision `4c1caa8` deployed in [run 46138124499](https://github.com/dystrophyless/pin/actions/runs/46138124499). The owner reported the text reply during the live test.

On November 28, the manual command follow-up passed 19 native profile/settings tests and 29 focused Telegram presentation and delivery tests on the Windows working tree based on `4c1caa8`. The native flow covers a multiword city, a zone already in use, invalid input, an active settings edit, and explicit save. Ruff, mypy, architecture checks, and `git diff --check` passed.
