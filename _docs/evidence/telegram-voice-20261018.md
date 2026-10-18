# Hosted Telegram voice smoke — 2026-10-18

**Result: both owner-provided recordings reached the actual hosted speech-to-planning path.** This is a bounded two-recording functional smoke, not a language-accuracy benchmark or acceptance of the complete core gate.

The owner explicitly approved transmitting these two originals to the Pin bot and their recognized text to OpenRouter within the existing 12-completion-attempt/USD 0.05 cap. Browser file selection remained unavailable. Instead, the authorized Bot API uploaded each original as a real `voice` message into the configured private test dialogue; root then used authenticated Telegram Web to forward that message back to the same bot as the user. The incoming forwarded messages produced actual gateway/speech jobs. Uploading bot-originated messages alone was not counted as ingress or transcription proof. No replacement recording or synthesized voice was used.

| Recording | Selected profile language | Observed result |
|---|---|---|
| Original RU; 17,728 bytes; decoded 5.02 seconds | RU | Client showed queued/transcribing notices, then asked whether to specify a time or leave the task untimed. Choosing untimed exposed the valid conflict with a requested timed reminder. Root cancelled the draft; the bot confirmed nothing was saved. |
| Original EN; 46,228 bytes; decoded 12.76 seconds | EN | Client showed queued/transcribing notices, then a task preview for 19 October at 14:00 Asia/Almaty / 09:00 UTC. The displayed default hour reminder was 13:00 local; the already-past calendar-day reminder was explicitly omitted. Root cancelled the draft; the bot confirmed nothing was saved. |

The second preview was client-observed on release `8482517`, after successful [CI/deployment](https://github.com/dystrophyless/pin/actions/runs/39142276137) and root-observed host health. It showed the corrected localized kind/reminder labels and local event-zone time. Suitable confirm/cancel and timing-choice buttons rendered. Processing notices were observed; the transient Telegram header typing animation was not captured and is not claimed here.

Read-only owner/window-correlated PostgreSQL checks found both speech requests in `continued`, with the matching original byte sizes and decoded durations, no failure codes, and the speech-owned temporary transcript fields cleared. A read-only check inside the running speech container found zero files remaining in its private audio directory. These observations do not claim deletion of the original local recordings, Telegram's copies or the separately retained conversation history.

The plan query for this voice-test window returned **zero new plans**. Provider receipts for the complete authorized session showed **5 calls / 5 turns, all reported, 0 unresolved, USD 0.000454323 total**, including the earlier three text calls. The timing-choice and cancellation callbacks introduced no additional completion call in this observed run. No Stars transaction occurred.

Root explicitly restored RU after the second draft was cancelled; the client confirmed the original Asia/Almaty, one-calendar-day/one-hour defaults, 22:00–08:00 quiet hours and 20:00 review. Other plans were not changed. The two short clips do not establish the separate 180-second boundary, broad RU/EN accuracy, noise/accent coverage, concurrency capacity or every voice/text ordering case; existing native/offline boundary evidence remains separate.
