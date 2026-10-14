# Telegram Stars payments

Pin bills its Telegram-only digital subscription in Telegram Stars. The
invoice link uses currency `XTR`, one price line for 250 Stars, an empty
provider token, and Telegram's 2,592,000-second recurring period. The current
Bot API exposes recurring `subscription_period` on `createInvoiceLink`, not
`sendInvoice`; Pin sends the generated link in a message with a URL button. The
invoice payload is an opaque, server-generated order key.

## Customer flow

1. `/terms` displays the price, renewal, cancellation, allowance, and manual
   refund terms. The user must then send `/acceptterms`.
2. `/buy` creates one owner-scoped order and queues its invoice. A repeated
   command reuses that order; an uncertain Telegram send is not retried
   automatically. If a worker stops during a provider call, its stale claim is
   fenced to `unknown` after two minutes and is not resent automatically.
3. Ingress commits Telegram's pre-checkout update before Billing validates the
   actor, order, accepted terms, XTR amount, and current entitlement. The
   webhook/poller carries a monotonic deadline from local receipt through
   persistence, validation, and the direct Bot API answer; validation reserves
   two seconds for the answer. If validation times out or the database is
   unavailable, checkout is denied. Pre-checkout approval changes no
   entitlement. A confirmed `successful_payment` receipt
   is validated and recorded in the same transaction that adds its provider-
   dated 30-day period.
4. Each accepted charge ID owns one paid period and one fresh allowance.
   Replays are idempotent. Renewal receipts use Telegram's expiration date;
   overlap, stale periods, wrong identities, amount/currency mismatch, and
   unconfirmed or non-recurring updates cannot add coverage.
5. Bare `/cancel` queues the Bot API renewal cancellation. A confirmed
   cancellation stops future renewal and keeps existing paid coverage through
   its end date. A task cancellation names its occurrence (`/cancel <UUID>`)
   and still requires the task preview confirmation; reminder Cancel
   buttons remain available. The same bare command cannot safely open a task
   menu for paid users because it is the approved renewal command. A general
   task-cancel menu or a disambiguation prompt remains an unresolved UX choice.
   `/paysupport` shows `PIN_PAYMENT_SUPPORT_CONTACT` when configured and asks
   the user for the Telegram charge ID. Do not request bank details in Telegram.

Telegram requires a pre-checkout answer within ten seconds after it sends the
query. The local monotonic deadline starts when this process receives the
webhook or polling batch; network and Telegram-side delivery delay before local
receipt cannot be measured or included. Its [Bot API](https://core.telegram.org/bots/api)
supports `editUserStarSubscription` for renewal cancellation and
`refundStarPayment` for refunds; the [Stars payment guide](https://core.telegram.org/bots/payments-stars)
documents subscription invoices and receipts. A failed or uncertain outbound
operation is recorded and is not retried blindly. A dedicated Test DC bot was
verified. Telegram accepted the test-only 250-Star invoice link at 300 seconds;
the invoice was sent and opened. No pre-checkout update, successful-payment
receipt, or paid period followed. The database snapshot is retained in the
local proof packet. The production 30-day period was rejected by Test DC with
`SUBSCRIPTION_PERIOD_INVALID`; the 300-second Test DC period does not prove the
30-day production contract. Two Stars top-up attempts in Telegram Desktop's
test client did not credit Stars; the test balance remained zero. A Telegram
Desktop collaborator said the test server does not provide free Stars in
[issue #29956](https://github.com/telegramdesktop/tdesktop/issues/29956), and
the request for a test Stars purchase/simulation mechanism was closed as not
planned in [issue #30037](https://github.com/telegramdesktop/tdesktop/issues/30037).
Those client reports and this UI attempt do not establish that every test
client or future top-up path is universally unavailable. A TDLib maintainer
confirms Test DC requires a 60- or 300-second period
([issue #3309](https://github.com/tdlib/td/issues/3309)). The adapter calls
type-check against locked aiogram 3.31.0;
the method and recurrence fields were checked against Telegram's official Bot
API documentation.

## Manual refund operation

Refunds are explicit operator actions; the application does not refund
arbitrary charges automatically. Review the immutable receipt and owner first,
then run the refund command in the configured private environment:

```powershell
uv run --frozen pin-billing-refund --charge-id <telegram-charge-id> --operator <operator-id> --execute
```

The command commits a unique refund intent, calls Telegram outside the database
transaction, and records `confirmed`, `failed`, or `unknown`. A confirmed full
refund removes only the period tied to that charge; independent periods and
plans remain. An `unknown` result needs provider/operator reconciliation and
must not be retried through this command. The receipt and minimal refund audit
remain available; this task does not invent a legal financial-retention period.

Before deployment, configure the real support destination, determine applicable
financial-record retention with the operator, and qualify the actual provider
account. No production invoice, charge, cancellation, or refund was issued.
