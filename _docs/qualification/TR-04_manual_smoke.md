# Current local RU/EN voice smoke

Owner scope:2026-10-16, SPEC-001v0.17. One original audio sample from the owner inRU and one inEN; no200-recording collection or400-request run. The approved reference library and recording kit are optional preparation only.

Status: **SELECTED-LANGUAGE TRANSCRIPTION COMPLETED; BOTH BOUNDED PROVIDER INTERPRETATIONS FAILED.** The earlier automatic-English failure remains recorded below. No plan or reminder was saved.

| Case | Source | Audio/hash | Local speech result | Downstream interpretation |
|---|---|---|---|---|
| OWNER-RU-01 | Owner, Russian | Original ending 02-02-50 | Selected `ru`; «Завтра напомни, сходить мне в аптеку.» (4.865s) | Schema-valid but semantically unsupported: added an unrequested explicit one-day-before reminder. |
| OWNER-EN-01 | Owner, English | Original ending 02-02-46 | Selected `en`; “Remind me to go to the drugstore tomorrow, 2pm” (5.091s) | Invalid output: fabricated appointment duration/fold and an invalid calendar-day/hour reminder; rejected before normalization. |

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


## Retry after owner freed memory

The same cached small CPU/INT8 model loaded successfully in 2.571 seconds. No model download, paid API call or saved plan was involved.

- Russian (5.02s original): automatic language `ru`, confidence 0.7589. Transcript: «Завтра напомни, сходить мне в аптеку.» Recognition took 7.095s in the diagnostic rerun. This is observed transcription, not an independently scored verbatim-reference result.
- English (12.76s original): automatic language `sl`, confidence 0.1884. Raw transcript: «Remeš mi, da v državstvu tomorom 2 p.m.» Production validation rejected this unsupported language. Diagnostic rerun took 8.019s. The automatic path failed; this observation must not be erased by the next diagnostic.
- Explicit-English diagnostic on that same original, with all other decode options preserved: “Remind me to go to the drugstore tomorrow, 2pm”. Recognition took 3.712s. This suggests language detection is the failure boundary; it is not proof that automatic inference or the product route is fixed.

At the time of the diagnostic above, no product source had changed and selected-language recognition was only a proposed correction. The owner then chose to use the persisted RU/EN profile language for voice recognition. The changed production adapter was run against both same original files with the selected language and cached small CPU/INT8 model (`cpu_threads=1`); both transcripts are shown in the current result table. This confirms these two local transcriptions only. No plan interpretation/date resolution, reminder, or full quality qualification was run. The earlier automatic `sl` detection and rejection remains a failed result and is not replaced by this selected-language diagnostic.

## Bounded selected-provider interpretation check (2026-10-16)

The owner authorized exactly two selected-provider completion calls, capped at $0.01 total. Both calls used the production-selected route `qwen/qwen3.7-flash`, with the production prompt/DTO/normalizer and a declared test reference instant of 2026-10-16 02:05 Asia/Qyzylorda. This is test context, not an actual receipt time. Under that context “tomorrow” is 2026-10-17. Before calling, the live catalog reported $0.00000003 per prompt token and $0.00000013 per completion token. The existing maximum-input bound (31,996 tokens) and 512-token output cap gave a conservative two-call upper bound of $0.00205288, below the authorized cap.

Exactly two calls were attempted; no retries occurred. Total recorded spend was $0.0002012730, with no unknown receipts. Both scenario strings in the capture were the original recognized transcripts: RU «Завтра напомни, сходить мне в аптеку.» and EN “Remind me to go to the drugstore tomorrow, 2pm”. The earlier terminal rendering that appeared to show replacement characters was misleading: the captured RU scenario string contains the original Cyrillic and zero U+FFFD characters. The temporary harness passed those strings as `ConversationContext.text`; the capture did not retain the separately serialized outbound request body, so exact wire bytes were not independently recorded.

| Case | Receipt | Usage and latency | Result |
|---|---|---|---|
| RU | `gen-1792099840-xfrtKpIPEeIs8o5F89Gl` | 3,403 prompt + 182 completion tokens; $0.0001446125; 2,688ms | DTO validation succeeded and normalized a task titled «Сходить в аптеку» for 2026-10-17 in Asia/Qyzylorda, untimed. Semantic failure: it also added an explicit one-calendar-day-before reminder, which the transcript did not request. |
| EN | `gen-1792099842-xCrzycYGnNYoSl0xKUBc` | 3,405 prompt + 184 completion tokens; $0.0000566605; 2,250ms | Invalid output. The provider returned 2026-10-17 14:00 in Asia/Qyzylorda but invented a 30-minute duration and `fold=later`; its `calendar_day` / `hour` relative reminder failed DTO validation. No normalized proposal was produced. |

These outcomes do not count as successful interpretation: RU is schema-valid but semantically unsupported; EN is rejected by validation. The declared test context resolves only the requested date and does not authorize adding a reminder. The allowance was held in memory only (RU settled; EN refunded after validation failure); no plan or reminder was saved. This two-sample check is not an accuracy or MVP qualification, and the earlier eight-call authorization remains exhausted.

## Offline prompt correction after observed failures

The conversation system prompt now distinguishes a task's requested date/time from a separate event-relative reminder. “Remind me tomorrow to go” means the task is scheduled tomorrow; an unrequested before-event offset is omitted so the profile defaults apply. An ordinary action at a requested date/time is a timed task, while appointments remain duration-bearing and their duration/end must not be invented. The typed validator was not weakened or changed; the observed `calendar_day`/`hour` output remains rejected.

Offline verification: the production adapter test accepts the timed-task shape with omitted reminders, asserts these grounding rules are present in the actual system prompt, and rejects the observed invalid reminder shape. `tests/test_model_adapter.py` passes 46/46; Ruff and mypy for the changed production module pass. The unchanged eight-call/$0.01 smoke limits remain in force; the updated prompt's conservative bound is 31,972 max input tokens (under the 32,000-token tier gate) and $0.00820576 worst-case exposure. No live model retest occurred; these prompt instructions do not prove provider behavior or reverse either failed sample.
