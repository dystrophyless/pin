# Whisper memory sizing — 2026-10-17

This is a bounded Windows x86-64 measurement, not an Ubuntu/AWS capacity qualification. No external API calls or task writes were made. The model was already cached; Hugging Face offline mode was enabled.

Configuration: production `load_speech_model()` defaults, faster-whisper small, CPU INT8, one CPU thread, one model worker, beam size 5, VAD enabled. The probe used the actual Pin decoder and recognition adapter for the two owner-provided RU/EN clips. A final 180-second synthetic waveform repeated the Russian clip through the same backend settings; it is a memory exercise, not representative human audio or an accuracy test.

| Stage | Resident working set MiB | Lifetime peak resident MiB | Private committed MiB | Lifetime peak committed MiB | Elapsed seconds |
|---|---:|---:|---:|---:|---:|
| Python baseline | 24.5 | 24.5 | 16.7 | 16.7 | — |
| Model loaded | 336.9 | 683.1 | 2253.0 | 2428.4 | 3.68 |
| English clip, 12.76 seconds | 385.3 | 683.1 | 2319.5 | 2454.7 | 5.61 |
| Russian clip, 5.02 seconds | 386.8 | 683.1 | 2320.4 | 2457.3 | 5.05 |
| Repeated Russian waveform, 180 seconds | 397.1 | 683.1 | 2330.5 | 2499.0 | 28.21 |

Windows GetProcessMemoryInfo reported process working set, private usage, peak working set and peak pagefile usage. Private committed memory is not resident RAM; the low working set on this memory-constrained machine must not be treated as a complete RAM requirement. The probe excludes the rest of the live Pin stack and does not establish paging volume, Ubuntu allocation behavior, AWS latency or sustained throughput.

Upstream's separate small/CPU/INT8/beam-5 benchmark reports 1477 MB RAM, with eight CPU threads on an i7-12700K: [faster-whisper benchmark](https://github.com/SYSTRAN/faster-whisper#small-model-on-cpu). Its environment and workload differ from this probe.

Current code uses one cached recognizer per speech-worker process. `run_speech_worker` awaits each claimed speech job before claiming the next. The proposed Compose topology starts one speech worker. Multiple users therefore share one model and queue; they do not each allocate another model. Additional speech-worker replicas would load additional models. Queue latency includes download, decoding, inference, capture continuation/provider work and existing queued jobs; these timings are not a service-level guarantee.

Sizing conclusion: do not recommend 2 GB as a demonstrated sufficient configuration for the complete stack. Use 4 GB as the initial conservative single-host candidate, one speech worker, then measure the full Ubuntu host before claiming capacity. This remains a sizing estimate, not proof that 4 GB supports any specified number of simultaneous arrivals. No server was provisioned or upgraded by this measurement.
