# Telegram ingress

The gateway accepts Telegram updates over one persistent transport per bot. The
selected mode is written to `pin_telegram.ingress_bots` on startup; a later
process cannot switch that bot from webhook to polling (or back) without an
explicit data migration. Webhook deployments configure
`PIN_TELEGRAM_INGRESS_MODE=webhook`, `PIN_BOT_TOKEN`, `PIN_WEBHOOK_SECRET`, and
`PIN_GATEWAY_HOST` / `PIN_GATEWAY_PORT`. Local owned-bot development can use
`PIN_TELEGRAM_INGRESS_MODE=polling`; the gateway also reads the dedicated test
bot token from `.env.local` when `PIN_BOT_TOKEN` is not set. That local file is
ignored by Git; its existing smoke configuration requires both
`PIN_BOT_TOKEN` and `PIN_TEST_CHAT_ID`. Set `PIN_DATABASE_URL` for either mode.

Run schema upgrades with `python -m alembic upgrade head`, then start the local
gateway with `python -m pin.entrypoints.gateway`. Use `--check` to validate its
configuration without making Telegram or database connections. Polling uses a
PostgreSQL advisory lock so only one local poller can own a bot token. Telegram
receives an update offset only after its receipt and work item commit. The
webhook returns success only after the same durable write commits; persistence
errors return a retryable `503`.

Callback spinner acknowledgments happen after receipt commit with a two-second
request timeout. Telegram rejection or network failure is logged without payloads
and does not undo the receipt, change webhook success, or hold the polling offset
and later payment updates. This acknowledgment is best effort; durable work remains
the source of processing truth.

Webhook requests are limited to 256 KiB, require JSON, and compare
`X-Telegram-Bot-Api-Secret-Token` in constant time. Telegram's declared update
fields are parsed and reserialized before storage, dropping unrecognized
payload fields such as a client-supplied `owner_id`. The bot identity is derived
from the configured server token. Private sender IDs are resolved by the
gateway's injected `TelegramOwnerResolver`; callbacks require a private message
whose chat ID matches the authenticated sender. Group messages and unbound
callbacks are acknowledged at transport level without creating owner mappings,
inbox receipts, or work.

Normal conversation messages remain sequenced. Voice and audio go to the
existing speech lane. Pre-checkout queries and successful-payment updates enter
the high-priority, unsequenced application lane so a speech lease cannot hold
up their validation handler. This is only a routing contract: ingress does not
approve a charge or send a payment confirmation. Payment approval policy stays
with the billing feature.

`pin_telegram.owners` currently stores an opaque, server-generated UUID per
`(bot_id, telegram_user_id)` so authenticated Telegram identities can use the
platform's owner key. Profile/account lifecycle work owns migration of this
mapping into account registration, deletion, and return behavior. Until that
handoff is implemented, do not infer account deletion or account-claim policy
from this transport mapping.
