# Telegram AI Life Organizer — Brainstorm Brief

Saved: 2026-10-13 (Asia/Qyzylorda)

This document preserves the brainstorm session for future specification and implementation. It is a product brief, not an approved implementation plan. Confirmed decisions, provisional recommendations, illustrative calculations, and open questions are distinguished below. No product code or infrastructure has been created during this session.

## 1. Product idea and audience

Build an AI assistant that helps people organize their lives, remember commitments, and follow through on their plans. Telegram is the only messenger planned for the first version.

Instead of manually maintaining a traditional to-do planner, users tell the bot what they need or intend to do through text or voice. The assistant turns that conversation into organized tasks, schedules reminders, and asks about completion.

Primary audiences:

- Forgetful people who miss appointments, commitments, or deadlines.
- People who need structure, deadlines, and accountability to complete their plans.

The initial promise is **reminders plus gentle accountability**, rather than intensive coaching. The assistant should also provide practical planning support.

## 2. Original example

A user says or records:

> I need to go to a check-up in Almaty this Friday at 14:00. Please remind me one day before and on this day, two hours before.

Expected flow:

1. Interpret the appointment, date, time, location, and reminder instructions.
2. Resolve the date and time zone; clarify ambiguity when necessary.
3. Save the appointment and reminders.
4. Confirm that it has been added to the user's plans and show the reminder schedule.
5. Send the day-before reminder on Thursday.
6. Send the two-hours-before reminder on Friday at 12:00 in the appointment's time zone.
7. Ask about completion after the appointment's expected end.

Unresolved: the exact time meant by “one day before” must be defined or clarified. The example establishes Thursday, but did not settle whether that means exactly 24 hours earlier or a preferred reminder time on the previous day.

## 3. Confirmed first-version scope

### Task types and planning help

- One-off appointments and tasks.
- Deadlines.
- Recurring tasks, such as exercising every Monday.
- Owner clarification, 2026-10-18: for Monday/Wednesday repeats, moving Monday to Tuesday with “this and future” replaces only Monday, giving Tuesday/Wednesday; the other weekday stays unchanged. Preserve the approved remaining-count and completed-history rules. See SPEC-001 TR-02 for the shared contract and verification fixtures.
- Untimed tasks that remain in a particular day's plan without a timed reminder.
- Help breaking tasks into steps, prioritizing, and rescheduling missed work.
- Daily and weekly plan views were requested; exact commands, layouts, and other periods remain to be designed.
- No external calendar integration in the first version. Suggestions rely on plans and preferences shared with the bot.

Recurring tasks can initially represent habits. Separate habit streaks, habit analytics, and intensive coaching are outside the selected first-version direction.

### Missing task times

When a user says “I need to exercise tomorrow” without a time:

- Ask for the missing time first.
- If known plans or preferences provide useful context, suggest suitable times through buttons.
- If there is only one suitable suggestion, ask the user to confirm it.
- Provide an option to keep the task untimed.
- Do not treat a gap in the bot's known plans as proof that the user is free.

### Reminders

- Set personal reminder defaults during onboarding.
- Apply those defaults when the user does not specify reminder instructions.
- Explicit instructions override the defaults.
- Show scheduled reminder times in the task confirmation.
- Scheduled reminders should be sent by application scheduling logic without requiring an AI call for every delivery.

### Completion check-ins

Timing depends on task type:

- Appointments: after their expected end; clarify duration when needed.
- Deadline tasks: after the deadline.
- Untimed tasks: during an evening review.

If the user ignores a completion check-in:

- Send **one extra nudge**.
- Include **Done**, **Reschedule**, and **Cancel** buttons.
- Silence leaves completion unconfirmed; it must not automatically mean completed or failed.

A design consideration identified in the session: for recurring tasks, cancellation should distinguish cancelling this occurrence from cancelling the whole series. Exact interaction remains to be specified.

### Quiet hours

- Quiet hours apply to default reminders and follow-up nudges.
- Explicitly requested reminder times take priority over quiet hours.
- Show conflicts in the confirmation so the user understands when a notification will arrive.
- Quiet-hour defaults and configuration UI remain open.

### Time zones

- Confirm a home/default time zone during onboarding.
- Allow a different time zone for a specific event.
- Allow users to change their default time zone.
- Clarify how existing plans should shift when the default changes.
- Do not assume automatic travel detection.

### Languages and voice input

- Russian and English at launch, including conversation, voice input, and date interpretation.
- Kazakh is a possible later addition, subject to recognition-quality testing.
- Maximum accepted voice-message duration: **3 minutes**.
- Handling oversized messages and the total monthly voice allowance remain open.

## 4. Telegram experience and landing website

Requested Telegram UX:

- Convenient natural-language text and voice input.
- Fast-reply buttons where they are useful, rather than on every bot message.
- Telegram “typing…” indicators while the bot is preparing a reply.
- Beautiful, organized plan displays using Telegram-supported text formatting.
- Clear task and reminder confirmations.

The user mentioned recently added Markdown features in Telegram. Exact supported formatting, parse mode, escaping rules, and rendering limits were **not verified** during the session. Verify against the current Telegram Bot API before implementation.

A public landing website is part of the product:

- Explain the product and its value.
- Explain the subscription/trial offer.
- Convert visitors into Telegram bot users.
- Serve as a referral entry point to the bot.

Website framework, branding, product name, visual direction, copy, and detailed conversion flow remain undecided. The working filename is not a product-name decision.

## 5. Confirmed technology choices

| Responsibility | Decision |
|---|---|
| Telegram bot | Aiogram |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Database migrations | Alembic |
| Conversational AI | Qwen3.7 Flash through an API |
| Local speech recognition | Multilingual Whisper `small` |
| Speech inference implementation | faster-whisper |
| Initial speech inference direction | CPU execution with INT8 compression, pending benchmark |

Use `small`, **not `small.en`**. The latter is English-only. Multilingual Whisper supports Russian and English and includes Kazakh in its language list. Supported language does not guarantee sufficient accuracy for this product.

Qwen3.7 Flash was explicitly selected by the user after discussing cost and suitability. OpenRouter was researched as an available access route; the final provider, account, region, and API integration were **not selected**.

The model supports tool calling and JSON output according to the researched provider page, but that page states JSON-schema enforcement is unavailable. The application must validate model-proposed task actions and data before applying them.

The backend should own persisted plans and reminder scheduling. Supplying only relevant tasks and conversation context to the model is the recommended cost-control direction; exact context management and tool contracts remain to be designed.

## 6. Launch and business model

Confirmed direction:

- Public paid launch, rather than an invited beta or permanently free product.
- A trial followed by a subscription.
- Desired subscription price: **around US$5/month**. This is a target, not a final validated price.
- Generous, clearly stated **monthly AI-chat and voice allowances**, rather than daily allowances or an unlimited promise.
- Exhausting an AI/voice allowance should not stop already scheduled reminders while the subscription remains active.
- Initial capacity target: **up to 50 daily active users**.
- Initial operating-budget target: **under US$50/month**, covering hosting, database, AI chat, and speech recognition.

Trial expiry without payment:

- Pause reminders at expiry.
- Warn users in advance.
- Clearly explain that tasks remain saved but reminders are inactive.
- Provide a clear way to resume after payment.

Unresolved business details:

- Trial duration and trial usage limits.
- Exact paid chat and voice allowances.
- Subscription currency, billing platform, Telegram payment requirements, and actual net proceeds.
- Whether and how paid-subscription expiry differs from trial expiry.
- Data retention after expiry, cancellation, and account deletion.
- Demand validation and willingness to pay.

The user explicitly raised uncertainty about model costs and affordability. Do not treat the public-paid direction or US$5 target as evidence that unit economics have been validated.

## 7. AI pricing and cost calculations

Pricing checked during the session on 2026-10-12. These are snapshots and must be rechecked before implementation or pricing commitments.

### Selected candidate: Qwen3.7 Flash

OpenRouter listed:

- Input: **US$0.03 per million tokens**.
- Output: **US$0.13 per million tokens**.

Illustrative calculation assuming **600 model calls per user per month**:

| Average tokens per model call | Input cost/month | Output cost/month | Total chat cost/month |
|---|---:|---:|---:|
| 5,000 input + 500 output | $0.09 | $0.039 | **$0.129 ≈ $0.13** |
| 10,000 input + 1,000 output | $0.18 | $0.078 | **$0.258 ≈ $0.26** |
| 20,000 input + 1,000 output | $0.36 | $0.078 | **$0.438 ≈ $0.44** |

These are examples, **not measured usage forecasts or guaranteed costs**. Include all billed output/reasoning tokens under the chosen provider's actual rules. One user message can require multiple model calls, particularly when tools, clarification, or retries are involved.

### Earlier comparison: Gemini 3.1 Flash-Lite

Before Qwen was selected, Gemini 3.1 Flash-Lite was considered at US$0.25/million text-input tokens and US$1.50/million output tokens.

At 600 monthly model calls:

| Average tokens per call | Illustrative monthly chat cost |
|---|---:|
| 2,000 input + 300 output | $0.57 |
| 5,000 input + 500 output | $1.20 |
| 10,000 input + 1,000 output | $2.40 |
| 20,000 input + 1,000 output | $3.90 |

The user challenged whether 2,000 input and 300 output tokens were realistic. The assistant acknowledged that the US$0.57 example was an optimistic scenario presented too confidently. Preserve that correction: **US$0.57 was neither a fixed fee nor a real per-user forecast**, and it does not describe the selected Qwen pricing.

All token examples exclude voice processing, hosting, payment deductions, taxes, backups, and other operating costs. A scheduled reminder or button action does not inherently require an AI call.

## 8. Local speech recognition and AWS hosting

The user has approximately **US$50 in AWS Free Tier/credit balance** and wants to use it initially. Its eligibility, expiry, account plan, and available services were not inspected in the account.

Provisional hosting recommendation: **AWS Lightsail Linux, public IPv4, 2 vCPUs, 4 GB RAM, US$24/month**.

Initial proposed layout, not yet implemented or approved as a full architecture:

- Bot/application and reminder processing.
- PostgreSQL on the same server initially.
- A separate queued speech-transcription worker, processing one recording at a time.
- Whisper small through faster-whisper with INT8 compression.

Researched machine comparison:

| Machine | CPU / RAM | Published price basis |
|---|---|---|
| Lightsail Linux 4 GB, IPv4 | 2 vCPUs / 4 GB | $24/month; 80 GB SSD; transfer allowance |
| Lightsail Linux 8 GB, IPv4 | 2 vCPUs / 8 GB | $44/month; 160 GB SSD; transfer allowance |
| EC2 t3.medium | 2 vCPUs / 4 GiB | About $30.51 for 730 hours at $0.0418/hour, Linux Northern Virginia; disk and public IPv4 additional |

Prices may change and EC2 prices vary by region. No AWS region was selected. The 8 GB Lightsail option adds memory but still lists two vCPUs; it is not automatically a solution to sustained CPU demand.

Evidence and limitations:

- faster-whisper's published Whisper small INT8 CPU benchmark used approximately **1,477 MB RAM** for transcription alone.
- That benchmark transcribed 13 minutes of audio in 1 minute 42 seconds using eight threads on an Intel Core i7-12700K.
- This is **not a latency or throughput prediction for a two-vCPU cloud server**.
- Lightsail uses CPU baseline/burst capacity; sustained transcription can exhaust burst capacity and reduce performance.
- Fifty daily active users does not establish simultaneous voice demand. Benchmark arrival rates and queues before making capacity promises.
- Self-hosted speech has no transcription API fee but still consumes paid compute resources.
- If eligible, $50 of credits would cover roughly two months of the $24 server alone; account-specific coverage and expiry must be verified.
- Backups, other AWS usage, AI API usage, and taxes are outside that server-only calculation.
- A GPU machine was not selected.

Qwen3-ASR-0.6B was considered as another local speech candidate. Its documented languages include Russian and English. The user subsequently selected Whisper small; Qwen ASR is not part of the confirmed stack.

## 9. Required validation before launch

These checks are needed to turn the current choices into reliable implementation decisions:

- Qwen Russian/English date interpretation, ambiguity handling, recurring schedules, and selecting the correct task for edits.
- Validating task/action JSON and preventing duplicate mutations during retries.
- Whisper accuracy on actual Telegram recordings, especially dates, times, numbers, names, noise, accents, and mixed languages.
- Speech latency, peak memory, CPU burst use, and queue behavior on the actual AWS machine, including three-minute recordings.
- Reminder reliability across application restarts and failures.
- Real token/call consumption for task creation, clarification, rescheduling, and weekly planning.
- Actual AWS credit eligibility and continuing costs after credits expire.
- Subscription unit economics including trial users, voice use, payment deductions, and backups.

No model-quality evaluation, server benchmark, reliability test, or actual unit-economics measurement has been completed.

## 10. Remaining specification decisions

- Product name, branding, and website technology/design.
- Onboarding sequence and exact reminder, quiet-hour, and evening-review defaults.
- Task confirmation policy: automatic save versus review before save, especially for multi-task voice input.
- Relative date semantics, “one day before,” and ambiguous time expressions.
- Recurring-task semantics, occurrence editing/cancellation, missed occurrences, and time-zone changes.
- Appointment duration defaults and the delay before the extra completion nudge.
- Exact daily/weekly plan presentation and Telegram formatting/button behavior.
- Handling overlapping plans, failed transcription, unavailable AI, and oversized recordings.
- User-visible behavior when a voice allowance is exhausted or transcription queues are long.
- Trial/subscription details and payment integration.
- Reminder scheduler, worker architecture, retries, idempotency, monitoring, and backups.
- Privacy, raw-audio retention/deletion, conversation retention, and account deletion.
- Capacity scaling and criteria for upgrading or separating services.

## 11. Research sources

These sources were consulted during the brainstorm. Revalidate mutable pricing and capabilities before relying on them for production.

- [Qwen3.7 Flash pricing, providers, and capabilities — OpenRouter](https://openrouter.ai/qwen/qwen3.7-flash)
- [Alibaba Cloud Model Studio pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Whisper models and multilingual variants](https://github.com/openai/whisper#available-models-and-languages)
- [Whisper supported language list](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py)
- [faster-whisper implementation and CPU benchmark](https://github.com/SYSTRAN/faster-whisper#small-model-on-cpu)
- [Qwen3-ASR models and supported languages](https://github.com/QwenLM/Qwen3-ASR)
- [AWS Lightsail instance bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)
- [AWS Lightsail billing and Free Tier information](https://docs.aws.amazon.com/en_en/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html)
- [AWS Lightsail CPU burst behavior](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-viewing-instance-burst-capacity.html)
- [AWS EC2 T3 specifications and published pricing](https://aws.amazon.com/ec2/instance-types/t3/)

## 12. Collaboration preferences for future work

- Brainstorm interactively with short responses.
- Ask **one question at a time**, using the request-user-input tool.
- Offer concrete options and explain their trade-offs.
- Produce a precise scope before beginning implementation.
- Carry confirmed choices forward while keeping provisional estimates and unresolved questions explicit.
- This save request authorizes this Markdown brief; it does not itself authorize deploying infrastructure, buying services, or implementing the product.
