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

Offline verification: the production adapter test accepts the timed-task shape with omitted reminders, asserts these grounding rules are present in the actual system prompt, and rejects the observed invalid reminder shape. `tests/test_model_adapter.py` passes 46/46; Ruff and mypy for the changed production module pass. The existing eight-call authorization remains exhausted; its preview bound is 31,972 max input tokens (under the 32,000-token tier gate) and $0.00820576 worst-case exposure. No live retest had occurred at this checkpoint; the later bounded retest is recorded separately below and did not reverse either failure.

## Bounded retest after prompt correction (2026-10-16)

The owner separately authorized two new completion calls, capped at $0.01 total. The earlier two-call authorization remains fully consumed and these are separate attempts; no retries or repair calls were made. The fixed reference remained 2026-10-16 02:05 Asia/Qyzylorda (declared test context, not a receipt time), so “tomorrow” meant 2026-10-17. A live catalog GET confirmed route `qwen/qwen3.7-flash` at $0.00000003/prompt token and $0.00000013/completion token. The 31,972-token input bound and 512-token output cap produced a two-call worst-case estimate of $0.00205144, below the new cap.

Before each request was handed to the provider transport, the harness captured the actual messages and normalized request JSON, decoded the serialized user context, and checked its transcript against the authorized source string. Both exact source strings survived; the RU transcript contained the original Cyrillic code points and neither transcript nor captured outbound payload contained U+FFFD. The local raw packet also retains the transcript code points and UTF-8 bytes, full outbound messages, normalized model payload, raw provider responses, and receipts: `C:\Users\roman\AppData\Local\Temp\pin-tr04-prompt-retest-4f8956dfe1e74d2cac4aec5f9dafc9a5.json`.

Exactly two new calls were sent to and returned from the selected route. Recorded spend was $0.0002047345, with no unknown receipts. Both outputs passed DTO validation but failed semantic review:

| Case | Receipt | Usage and latency | Retest outcome |
|---|---|---|---|
| RU | `gen-1792102617-X9qOf6UoRKz9ofXQU4BQ` | 3,373 prompt + 182 completion tokens; $0.0001435775; 2,688ms | Title «Сходить в аптеку» and task date 2026-10-17, untimed. It again added an unrequested explicit one-calendar-day-before reminder. Semantic failure. |
| EN | `gen-1792102619-Z7vdLEqXFcn13Lx7434a` | 3,375 prompt + 221 completion tokens; $0.000061157; 2,797ms | Correct date/time 2026-10-17 14:00, but returned an appointment with invented 60-minute duration plus an explicit 30-minute-before reminder and `reminder_mode=override`, neither requested. No fold was returned. Semantic failure. |

The RU/EN schema-valid outcomes therefore remain unsuccessful despite correct dates and the absence of the former invalid calendar-day/hour unit. The timed-task prompt correction did not make the EN output a task, and neither language stopped inventing a distinct reminder. In-memory allowances settled for both valid structured outputs; no plan or reminder was persisted. The initial failures above remain unchanged. This two-example retest is not an accuracy or MVP qualification, and no further calls are authorized by this test.

## Concise few-shot prompt redesign (2026-10-16; offline only)

After both bounded retest cases still failed semantic review, the system prompt was rewritten with a compact field contract, a short decision checklist, and five RU/EN examples for task-date versus reminder-offset, timed task versus appointment, explicit appointment duration/reminder, missing-duration clarification, and using a listed target id/version. It no longer embeds the generated 8,006-character JSON Schema. The provider request remains `response_format={"type":"json_object"}`; the Pydantic result schema and strict validation are unchanged. The prompt does not request a reasoning field or expose a rationale in the output. No provider/model, parser, limits, or smoke scenario changed.

Before: 14,458 system-prompt characters (18 lines). After: 6,258 characters (16 lines), about 57% shorter. Offline smoke estimate at the helper's configured prices: maximum input bound 23,864 tokens, output cap 512, eight-call ceiling, worst-case $0.00625984. The previous estimate was 31,972 input tokens and $0.00820576. This is a local preview, not a current catalog check or live call; the earlier eight-call authorization remains exhausted. No retest was run, and both recorded semantic failures remain failures pending separately authorized evaluation.

Offline checks after the rewrite: `tests/test_model_adapter.py` 46/46; Ruff passed for the changed source and test; mypy passed for the changed production module; architecture import-boundary check passed. These checks validate the prompt contract and unchanged output path only, not model behavior.

## Bounded few-shot retest (2026-10-16)

The owner authorized two fresh completion calls, at most $0.01 total; earlier call budgets remain separate and exhausted. The harness was copied to a new temporary file and used the production prompt, route, adapter, strict DTO validation, and in-memory allowance/usage recorders. It performed one current public catalog GET before completion calls. The selected route remained `qwen/qwen3.7-flash`, priced at $0.00000003/prompt token and $0.00000013/completion token. The 23,864-token input upper bound and 512-token output cap gave a conservative two-call exposure of $0.00156496. The declared test instant was 2026-10-16 02:05 Asia/Qyzylorda (not an actual receipt time), so “tomorrow” resolved to 2026-10-17.

Exactly two calls were attempted, one per source transcript, with no retry or repair call. The serialized outbound messages were captured before transport. Both user strings exactly matched the authorized source; the Russian message retained its original Cyrillic code points and UTF-8 bytes, and neither outbound payload contained U+FFFD. Both requests and responses used the selected route. Recorded spend was $0.00012482; receipts were known and no unknown-cost state occurred. Each in-memory allowance was reserved and settled. No plan or reminder was persisted.

| Case | Receipt | Usage and latency | Result |
|---|---|---|---|
| RU | `gen-1792130473-E37pUsqcMzLZFVBnQTLi` | 1,607 prompt + 95 completion tokens; $0.00006056; 2,234ms | Strict validation passed. The model proposed an untimed task for 2026-10-17 with no explicit reminder. Semantic failure against AC04: it selected `untimed` directly instead of returning `timing=unspecified`, which Capture uses to offer the timed/untimed choice when the request has no hour. |
| EN | `gen-1792130475-UqVUDi8C952PMtLl9XMv` | 1,609 prompt + 123 completion tokens; $0.00006426; 4,500ms | Strict validation and sample semantics passed: timed task for 2026-10-17 at 14:00 Asia/Qyzylorda; no invented appointment duration, fold, explicit reminder, or reminder-mode override. Profile defaults remain omitted. |

This confirms the expected date/time grounding for the English sample but does not resolve the Russian missing-time choice behavior. The RU result did not save a plan, and the result is not counted as a passing RU interpretation. Earlier failed runs remain unchanged. The two-sample retest is not a broad accuracy qualification, and these two new calls exhaust this authorization. Full local packet: `C:\Users\roman\AppData\Local\Temp\pin-tr04-fewshot-retest-be91077256974ba0aaead8fc485ccd4a.json`; fresh harness copy: `C:\Users\roman\AppData\Local\Temp\pin-tr04-fewshot-retest-b9ff96c6bdad4261a55e23dae1c31dde.py`.

## Offline AC04 prompt correction after the bounded few-shot retest (2026-10-16)

The latest Russian sample selected `untimed` directly, so it did not reach Capture's timed/untimed choice. The prompt now says that untimed requires an explicit user choice, including a prior clarification; a task date without an hour must remain `unspecified` so Capture offers the choices. The Russian few-shot rationale reinforces that rule, and its JSON still uses `timing=unspecified`. The schema, DTO validation, and runtime behavior are unchanged. That offline check did not establish model behavior; the earlier Russian direct-untimed result remained a semantic failure at this checkpoint. A later single-call Russian retest is recorded below. The English result is only a passing sample, not general quality evidence.

Offline checks: `tests/test_model_adapter.py` 47/47; Ruff, production-module mypy, architecture check, and `git diff --check` passed. The prompt is 6,475 characters over 16 lines. Offline smoke preview: 24,081 input-token upper bound, 512 output tokens, eight-call ceiling, $0.00631192 worst-case at helper-configured prices. This is not a current catalog check or a live model result.

## Single-call Russian AC04 retest (2026-10-16)

The owner authorized one Russian-only completion call, capped at $0.01. A fresh copy of the outbound-capturing harness hard-asserted exactly one RU scenario and a one-call limit before preflight. One current public catalog GET confirmed the selected route `qwen/qwen3.7-flash` at $0.00000003/prompt token and $0.00000013/completion token. With a 24,081-token input upper bound and 512-token output cap, the one-call worst case was $0.00078899. The declared test instant was 2026-10-16 02:05 Asia/Qyzylorda (not a receipt time), making “tomorrow” 2026-10-17.

Exactly one completion call was attempted and returned from the selected route. The actual outbound serialized user text matched the authorized Russian transcript; the packet retains its Cyrillic code points and UTF-8 bytes, with no U+FFFD in the outbound payload. Receipt `gen-1792130796-rlXAAcGoGqk43o89uXhM` recorded 1,652 prompt + 95 completion tokens (1,747 total), cost $0.00006191, latency 2,891ms; no unknown receipt occurred. The in-memory allowance was reserved and settled. No plan or reminder was saved.

The strict-valid proposal was a task titled «Сходить в аптеку» for 2026-10-17 with `timing=unspecified` and no explicit reminder. This sample meets AC04's expected handoff: Capture should offer the timed/untimed choice. Capture itself was not executed, so the choice interaction is not claimed as observed. This is one Russian sample only; earlier failures remain preserved and this is not a broader quality qualification. This call exhausts the one-call authorization. Full local packet: `C:\Users\roman\AppData\Local\Temp\pin-tr04-ru-ac04-retest-77bed915b2a5440db98410ccdd7503ac.json`; fresh harness copy: `C:\Users\roman\AppData\Local\Temp\pin-tr04-ru-ac04-retest-efe4f6aa2ddd409cb2823ecd9dbf30a7.py`.
