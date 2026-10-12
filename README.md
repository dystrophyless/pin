# Pin

A Telegram AI assistant that helps people organize their lives, remember commitments, and follow through on their plans.

Users describe tasks through text or voice. The assistant organizes one-off and recurring plans, sends reminders, and checks completion with gentle accountability.

## Project status

Product discovery and planning. The application has not been implemented yet.

See [the brainstorm brief](PROJECT_BRAINSTORM.md) for agreed product behavior, technology choices, cost assumptions, research sources, and open questions.

## Selected stack

- Aiogram for the Telegram bot.
- PostgreSQL, SQLAlchemy, and Alembic for persistence and migrations.
- Qwen3.7 Flash for conversational AI.
- Multilingual Whisper small through faster-whisper for local Russian and English voice transcription.

Voice messages will be limited to three minutes. AWS Lightsail is the provisional hosting choice, subject to benchmarking.

A landing website will explain the product and direct visitors to the Telegram bot.
