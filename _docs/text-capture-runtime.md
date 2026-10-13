# Text capture runtime

The application worker hands completed-onboarding private text to Conversation capture after preserving agenda and onboarding command routing. The worker stores a stable `telegram-inbox:<inbox UUID>` request key, opens the capture input/draft fence in a short SQL transaction, and closes that transaction before calling `BoundedPlanningAssistant`. Provider usage and AI-turn allowance use the existing Conversation and Billing adapters; retries reuse the same request key so a completed normalized response may replay while an unknown provider outcome is never submitted again automatically.

Capture confirmation, cancellation, and clarification buttons are deterministic application-worker actions. Confirmation resolves the owner-bound capture credential, rechecks current preferences/access and the exact preview proofs, then writes one-off and recurring plans, receipts, notification events, and the confirmed draft in the caller's transaction. Cancellation and choices use the common owner-bound Telegram callback-token store with the current capture draft version. None of these button actions invokes the model.

Capture responses are rendered from the persisted response projection. Each rendered page becomes its own `telegram.reply.requested` outbox event; only the last page carries its confirm, cancel, or clarification keyboard. Every event includes the source job identity, page index/count, and the standard chat/Markdown/plain-text fields consumed by Delivery.

`pin-app-worker --check` validates configuration and exits before creating the provider client. It does not call OpenRouter. Runtime integration tests use an injected assistant and a dedicated disposable PostgreSQL database; they provide no live-model qualification evidence.
