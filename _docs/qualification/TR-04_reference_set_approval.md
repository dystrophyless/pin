# TR-04 reference-set approval

Owner decision:2026-10-16 (Asia/Qyzylorda).

The owner explicitly selected **“Утвердить тексты и ожидаемые даты”** after the recurring-task addition. This approves the revised script composition and expected temporal calendars for the future evaluation:100distinct RU scripts and100distinct EN scripts, paired into100slots in each ofRUtext,RUvoice,ENtext,ENvoice (400slots total). Each language includes10elapsed-from-receipt and10finite-recurring scripts; their80RU/ENoccurrence dates are included in the approved references.

The approved artifact is [TR-04_evaluation_candidates.jsonl](TR-04_evaluation_candidates.jsonl), at Git revision`de28299`, blob`76909adc9dabb22beccb2d2169c636328d10b223`, Git-blob UTF-8/LF SHA-256`8e825856284f6e52aadcf63edd689122ae27e85df78053724f7c54b3d2bebd40`. Use these exact bytes/content for reproducibility; checkout line-ending conversion is not a content amendment. The JSONL is left unchanged so the approval binds the reviewed artifact. Its embedded draft/awaiting-audio labels record generation/collection state; this approval supersedes the text/reference approval state only.

No voice recordings exist in this manifest:all audio references are null. Audio collection/eligibility and acoustic sampling still require a concrete protocol. Script approval does not establish measured accuracy, real-world representativeness, model/host suitability, workload/latency approval or overall TR-04 PASS. It does not authorize recording activity, paid/external API calls, additional model spend, host work or deployment. The approved99% request-level scoring and four independent100-request groups remain unchanged.

The earlier12-row small example file is separate and is not implicitly approved or counted in the400slots. Semantic changes to the approved scripts or expected calendars need a new recorded decision; do not silently replace approved samples or overwrite failed observations.

## Audio-source decision —2026-10-16

The owner subsequently selected **“Реальные записи людей”** for the200voice examples (100RU/100EN). Use human-spoken original recordings, not local speech synthesis or a mixed synthetic set. No recordings have been supplied or collected by this decision. The [recording kit](recording_kit/README.md) derives the200voice scripts and an empty tracker from the approved immutable reference set. Participant count, acoustic sampling and collection/eligibility/handling protocol remain pending; no additional API spend or measured quality follows.
