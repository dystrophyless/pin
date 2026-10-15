# Current local RU/EN voice smoke

Owner scope:2026-10-16, SPEC-001v0.16. One original audio sample from the owner inRU and one inEN; no200-recording collection or400-request run. The approved reference library and recording kit are optional preparation only.

Status: **TWO ORIGINAL FILES RECEIVED; DECODING PASSED; ASR BLOCKED BY MODEL-LOAD MEMORY FAILURE.** Language-to-file mapping is not yet established. No transcript, model proposal, accuracy score or live-user plan exists.

| Case | Source | Audio/hash | Local speech result | Downstream interpretation |
|---|---|---|---|---|
| OWNER-RU-01 | Owner, Russian | Mapping pending transcription | Model load failed; not transcribed | Not run |
| OWNER-EN-01 | Owner, English | Mapping pending transcription | Model load failed; not transcribed | Not run |

Use one clear task with a date/time in each recording. The owner may read the same request in both languages. Preserve the actual original audio and stable per-sample receipt/reference metadata; explicitly record the test timezone and reference calendar for relative phrases. Do not infer a production home zone from the language. Declared test context is not proof of Telegram upstream arrival time.

Run cached localWhisper small against the actual files, measuring actual format/size/decoded duration and recording transcript/language. Confirm whether task/date/time survive transcription; clearly distinguish this observation from an actual selected-provider proposal. Keep prior8-call/$0.01authorization exhaustion intact; no silent provider-budget reset or broad model/host run. Any downstream call needs applicable bounded authorization and actual receipt/accounting. No plan is saved or reminder scheduled merely by this diagnostic check.

Report each observed result and failure plainly. Two cases cannot establish99%accuracy, cross-speaker/noise representativeness, the previous400-item benchmark, selected-host capacity or fullMVPacceptance. Do not count absent audio or unrun stages as successful.


## Received originals and actual attempt (2026-10-16)

Both originals remain outside Git in the owner's Downloads directory. Existing `PyAVAudioDecoder` successfully decoded both files; durations below are measured decoded audio, rounded for display.

| Original filename | Bytes | Decoded seconds | SHA-256 |
|---|---:|---:|---|
| audio_2026-10-16_02-02-46.ogg | 46228 | 12.76 | 5fa8c0be46400296b5c9327d9bf8f51f40cc9421c65c4a5a8ce1611926f00c06 |
| audio_2026-10-16_02-02-50.ogg | 17728 | 5.02 | c7d84ad791d2c3a26c15aa73f1feca770716799369b75b55542e8b8aa6bd926a |

Cached `WhisperModel('small', device='cpu', compute_type='int8', num_workers=1, cpu_threads=2, local_files_only=True)` failed during model construction with `RuntimeError: mkl_malloc: failed to allocate memory`. Offline environment flags were enabled; no download or provider request occurred. At the subsequent host observation Windows reported 592772 KiB free physical memory and 1800236 KiB free virtual memory. No transcription was attempted after construction failed. The independent decoding check then passed for both originals.

Next step: retry the same cached model after memory becomes available. Do not substitute another model or mark either language successful. Relative-time reference context remains to be recorded if the actual transcripts require it. This attempt does not establish Telegram delivery behavior or end-to-end planning quality.
