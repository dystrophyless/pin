# Hosted landing browser observations — 2026-10-18

The deployed landing was inspected in Edge against the running preview. Russian and English pages rendered at the desktop viewport and at 390 × 844. At the mobile viewport, document width was 375 pixels in each language, with no horizontal overflow. Screenshots of all four language/viewport combinations are retained in the private operator packet.

Keyboard activation of the Russian skip link moved focus to `main#main`. Changing the language control updated the document language, visible copy, and localized navigation. Privacy and terms links were followed in both languages; each opened the expected localized page. Observed Telegram links point to the approved public bot, `https://t.me/pin_appbot`.

The pages disclose their preview status and unresolved offer/provider eligibility. They describe the approved trial, subscription allowances, cancellation, expiry, confirmation and retention limits without asserting a financial-record retention period. These browser observations do not establish legal approval, commercial eligibility, payment behavior, complete accessibility conformance, or successful support-command execution. The missing persisted `/help` and `/support` runtime route was subsequently corrected, tested and deployed; see the separate [operations evidence](operations-acceptance-20261018.md).

No payment, analytics setup, account change, or provider call was performed for this inspection. The temporary mobile viewport override was reset.

## Direct referral verification

Root subsequently clicked the deployed English primary CTA, observed the Telegram launch page titled `Telegram: Launch @pin_appbot`, returned to the landing, and clicked its support link. Both navigations reached the same intended bot and exposed `START BOT` with destination `tg://resolve?domain=pin_appbot`. This verifies web referral navigation, not a fresh bot conversation. No message was sent.

Visible offer values match SPEC-001 O11/O12/O14: seven-day trial after setup, 250 trial AI turns and 15 voice minutes; 250 Stars per 30 days, 1,000 paid AI turns and 60 voice minutes; three-minute voice-message limit; auto-renewal with cancellation retaining access through the paid period. Expiry/reminder and preview-before-save wording were inspected in both languages. Provider eligibility and statutory retention remain separate release gates; the preview states those limits explicitly.
