# Private Telegram voice capture

Private Telegram voice messages enter the Speech lane using the original inbox record and
per-owner conversation sequence. The worker reserves one AI turn plus the declared voice
seconds before contacting Telegram, downloads only the file returned by Telegram's trusted
`get_file` lookup, and bounds the streamed file to 10,000,000 bytes. PyAV decodes once to a
mono 16 kHz waveform; decoded duration must be at most 180 seconds and the open usage hold is
resized to the measured billable duration before local inference.

The worker lazily loads one multilingual Whisper `small` CPU INT8 model on the first eligible
transcription, then reuses it for later jobs. It accepts Russian and English recognition and
passes the transcript into the existing capture clarification and preview flow. No voice
transcript directly writes a plan. The original speech lease fences transcript publication and
the atomic capture continuation. Later messages remain ordered behind that voice item.

Temporary paths are generated and private. Completed or failed processing removes its file in
the worker's `finally` block. Worker startup and hourly checks remove only generated audio files
older than 24 hours. Recognition failures retain measured audio facts and refund the open
allowance; successful capture interpretation settles the combined AI and voice reservation.

The local feature checks are:

```powershell
uv run pytest tests/unit/speech
uv run python scripts/verify_speech_fixtures.py
```

The guarded PostgreSQL worker integration test additionally requires the assigned
`PIN_TEST_SPEECH_DATABASE_URL`; it exercises private ingress, actual local Whisper recognition,
usage settlement, continuation ordering, quota rejection before download, and failure refunds.
