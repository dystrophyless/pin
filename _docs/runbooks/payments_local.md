# Local payment support and refund operations

This runbook describes the operator path implemented by `pin-billing-refund`. A Telegram receipt is the source of charge ownership and amount; `/paysupport` asks the user for the Telegram charge ID and says that a person reviews refunds. Do not collect bank-card data in Telegram support.

## Before a refund

1. Open the immutable Billing receipt and confirm the exact Telegram charge ID, owner, Telegram user, amount, currency, and the period linked to that charge. Compare the user's report with the receipt and payment history; refund review is manual.
2. Confirm that the request is an eligible duplicate or incorrect charge and that the operator is authorized to act. Record the internal operator identity. The command issues a full refund; it is not a quote, partial-refund, or preview tool.
3. Run the command in the configured private operator environment only after reviewing the exact charge and owner:

   ```powershell
   uv run --frozen pin-billing-refund --charge-id <telegram-charge-id> --operator <operator-id> --execute
   ```

   `--execute` is required. This command calls Telegram. Never run it with a real charge while doing local route verification.

## Read the outcome

The command first commits one refund intent, calls Telegram outside the SQL transaction, and records the result.

- `confirmed`: verify the persisted refund audit and linked access projection. Only the period associated with that charge is removed; unrelated paid coverage and plans remain.
- `failed`: inspect the recorded failure and receipt before deciding on another action. Do not assume a failed CLI process means no provider-side effect.
- `unknown`: stop. Reconcile the provider's charge state and the persisted refund record with an authorized operator. Never rerun the command blindly for an unknown result; a request whose result is uncertain may already have succeeded.

Keep the immutable receipt and minimal refund audit available for reconciliation. The operating country is Kazakhstan, while the entity form (IP versus TOO), tax treatment, required financial-document categories, and applicable retention schedule remain unresolved. Do not invent a retention duration or erase financial records until those requirements are validated.
