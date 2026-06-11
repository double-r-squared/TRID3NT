# job-0277 — envelope case-tagging (kills the wrong-stream display limit)

**Problem (documented job-0269, user-confused twice live):** live streaming
envelopes routed to the stream the user LAST MESSAGED (web submit-time
`targetKey`). With stream-scoped turn concurrency, a still-running turn's
cards/narration painted into whichever Case the user touched next — "it
just went to publishing without geocoding" was another Case's turn.

**Design (proposed A.1 amendment — user lands the SRS change):**
- contracts: optional `Envelope.case_id` (wire-compatible; None = untagged).
- agent: `bind_turn_case` ContextVar (pipeline_emitter.py) bound by both
  dispatch wrappers at task entry with the job-0268 turn pin; BOTH envelope
  construction sites (`server._new_envelope`, `PipelineEmitter._send`) stamp
  it. ContextVars are per-task: concurrent turns cannot cross-tag.
- web: `handleMessage` extracts the tag; `dispatchEnvelope`/hub fan-out
  carry it; streaming handlers (chunks, pipeline-state, session-state,
  error, charts, code-exec) gain an optional `caseId` arg; Chat's route*
  functions route tagged envelopes to `streamKeyFor(case_id)` with
  untagged → targetKey fallback (old builds, root turns).

**Evidence:** agent 5 new tests (tagging, untagged-outside-turn, concurrent
ContextVar isolation, emitter stamping, wrapper binding) — full suite 4322
passed / 5 known-pre-existing; web 4 new routing tests — vitest 590;
contracts 391.
