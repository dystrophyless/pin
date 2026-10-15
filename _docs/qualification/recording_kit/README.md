# TR-04 human-recording kit

**Optional preparation, not the current required check.** The owner narrowed the local check to oneRU and oneENrecording from the owner; see[the manual smoke worksheet](../TR-04_manual_smoke.md). Do not collect200clips or recruit speakers under the current request.

**Preparation only:200script rows,0audio recordings,0evaluation results.** The owner selected real human recordings on2026-10-16. Speech synthesis is outside this selected voice set. Reference scripts and temporal calendars were separately approved; [approval record](../TR-04_reference_set_approval.md).

Sources:[unchanged reference JSONL](../TR-04_evaluation_candidates.jsonl) at`de28299`, Git-blob SHA-256`8e825856284f6e52aadcf63edd689122ae27e85df78053724f7c54b3d2bebd40`. This kit is a generated collection aid, not a second requirements source. It has100RU and100ENvoice IDs corresponding to the original400evaluation slots; text inputs require no recording.

Use[RU scripts](RU_scripts.md),[EN scripts](EN_scripts.md) and[the empty recording tracker](recording_manifest.csv). The CSV is UTF-8 with BOM for Windows readers. Read only the exact utterance, not IDs/reference timestamps. The reference clock belongs to the controlled test context; do not change dates to today or create real appointments while recording.

Record one original human-spoken clip per ID, named for example`RU-V-001.ogg`or`EN-V-001.wav`. Keep the original recording and format. Each product input must be within180decoded seconds and10MB; duration/size eligibility will be verified, not inferred from the filename. No microphone capture or upload is implemented or triggered by this kit.

Use only willing participants who permit use of their voice for this Pin evaluation. Track a local speaker alias and permission note; do not add private conversations, real medical details, credentials or unnecessary personal identifiers. Keep raw audio locally outside Git; do not publish or commit it. No new retention duration is chosen here.

Fill`audio_file`,`speaker_alias`,`recording_condition`and`consent_note`when eligible audio is actually available. Record actual file hash/duration after measurement. Empty cells are unknown, not zero or evidence of consent/eligibility. Speaker count, acoustic-condition mix, collection/eligibility review and handling protocol still need a concrete decision; this kit does not silently select them.

Keep IDs stable and preserve mistakes/retakes with distinct attempt names/status for review; do not silently overwrite a frozen or scored observation. Do not replace failed model results with fresh successful samples. Update a separate collection manifest, leaving the approved source JSONL unchanged. No actual API/model evaluation, provider budget, host measurement or TR-04 PASS is authorized or claimed.
