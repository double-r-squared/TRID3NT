# Report: Real pipeline-state + session-state.loaded_layers emission (closes OQ-T-28-SIM-WS-BOUNDARY)

**Job ID:** job-0035-agent-20260606
**Sprint:** sprint-06
**Specialist:** agent
**Task:** Land a `PipelineEmitter` class in `services/agent/src/grace2_agent/pipeline_emitter.py` that holds the current `PipelineSnapshot` and broadcasts `pipeline-state` envelopes on every step transition (replace-not-reconcile per Appendix A.7); wire it into the existing `server.py` tool-call site so each `TOOL_REGISTRY[name].fn(...)` invocation auto-creates a step; populate `session-state.loaded_layers` when a tool returns a `LayerURI`; demonstrate live with a real WS transcript (initial `session-state` → ≥3 `pipeline-state` snapshots → final `session-state` with `loaded_layers` populated). Closes OQ-T-28-SIM-WS-BOUNDARY.
**Status:** ready-for-audit

## Summary

Landed `PipelineEmitter` (~450 lines) owning one session's pipeline snapshot + `loaded_layers` accumulator, with 6 transition methods (`add_step`, `mark_running`, `update_progress`, `mark_complete`, `mark_failed`, `mark_cancelled`) plus a higher-level `emit_tool_call` wrapper that the `server.py` tool-call site uses to bracket each `TOOL_REGISTRY[name].fn(...)` invocation. Replace-not-reconcile (A.7) is structurally enforced: there is **no** `merge` / `apply_delta` / `update_partial` / `reconcile` method on the class, and `test_no_merge_helper_exists` scans `dir(PipelineEmitter)` to keep it that way. `session-state` is re-emitted with the accumulated `loaded_layers` whenever an `emit_tool_call` invocation returns a `LayerURI`; the `current_pipeline` cross-envelope field (job-0026's predicate-b feeder) is populated whenever a pipeline is open and cleared after `close_pipeline`. The `server.py` tool-call site is a minimal addition (no refactor of unrelated WS code per FROZEN): `_invoke_tool_via_emitter` + a `/invoke <tool> <json>` debug directive on `user-message` that drives the M4 emission seam end-to-end so the M4 live-evidence harness exercises the real path. 11 new emitter tests pass; the agent suite is 57/57 green; contracts 131/131 unchanged. The live WS transcript captures 13 frames (4 `session-state` + 9 `pipeline-state`) from a real socket round-trip through three `/invoke` directives — closes **OQ-T-28-SIM-WS-BOUNDARY** for job-0036.

## Changes Made

- `services/agent/src/grace2_agent/pipeline_emitter.py` (NEW, ~450 lines)
  - `ErrorCodeRegistry` + module-level `EMITTER_ERROR_CODES` seeded with `UPSTREAM_API_ERROR`, `BBOX_INVALID`, `GEOCODE_NO_MATCH`, `TOOL_NOT_FOUND`, `TOOL_PARAMS_INVALID`, `CANCELLED`, `INTERNAL_ERROR` (open-set SCREAMING_SNAKE_CASE per Appendix A.6).
  - `EmitterError` / `StepNotFoundError` (emitter-internal errors, distinct from tool errors).
  - `PipelineEmitter(session_id, sink, *, chat_history=None, pipeline_history=None, map_view=None)`:
    - `start_pipeline()` → fresh `pipeline_id`; `close_pipeline()` archives into `pipeline_history` and clears `current_pipeline`.
    - `add_step(name, tool_name) -> step_id` (auto-opens a pipeline if needed; emits `pipeline-state` with the new pending step).
    - `mark_running(step_id, *, progress_percent=None)` (stamps `started_at`; emits).
    - `update_progress(step_id, progress_percent)` (M5+ solver opt-in; atomic tools never call this — per kickoff TENTATIVE).
    - `mark_complete(step_id)` (stamps `completed_at`; emits).
    - `mark_failed(step_id, error_code, error_message)` (registers `error_code` with `EMITTER_ERROR_CODES`; truncates `error_message` to 512 chars per D.6; emits).
    - `mark_cancelled(step_id)` (Invariant 8 — distinct from failed; emits).
    - `add_loaded_layer(layer: LayerURI)` (translates to `ProjectLayerSummary`, dedups by `uri`, emits a fresh `session-state` per A.7).
    - `emit_session_state()` (serializes `chat_history`, `loaded_layers`, `pipeline_history`, `current_pipeline`, `map_view` into one full snapshot).
    - `emit_tool_call(*, name, tool_name, invoke)` — the integration seam `server.py` uses: `add_step` → `mark_running` → invoke (awaits if coroutine) → on `LayerURI` return: `add_loaded_layer` → `mark_complete`; on `asyncio.CancelledError`: `mark_cancelled` + re-raise; on other exceptions: `_classify_exception` → `mark_failed` + re-raise.
  - `_classify_exception` maps known exception types to the open-set A.6 codes (`ConnectionError → UPSTREAM_API_ERROR`, `ValueError with "bbox"` → `BBOX_INVALID`, `LookupError with "geocode"` → `GEOCODE_NO_MATCH`, `KeyError with "tool"` → `TOOL_NOT_FOUND`, fall-through `TypeError`/`ValueError` → `TOOL_PARAMS_INVALID`, default → `INTERNAL_ERROR`).
  - `current_snapshot()` materializes a D.6 `PipelineSnapshot` from the internal `_StepState` store; the wire `PipelineStep` form (A.4) is materialized by `_to_wire_step`. Both shapes are independent — the D.6 form carries the new `progress_percent` / `error_code` / `error_message` from job-0030; the wire A.4 form carries only `progress_percent` (the A.4 schema doesn't expose error fields, which surface in `session-state.current_pipeline`).

- `services/agent/src/grace2_agent/server.py` (EDIT — tool-call site integration only, no refactor of unrelated WS code per FROZEN):
  - Added `PipelineEmitter` import + `TOOL_REGISTRY` import.
  - Extended `SessionState` with `emitter: PipelineEmitter | None = None` (M1 `current_pipeline_id` / `current_pipeline_steps` kept untouched so the M1 LLM-stream path stays unchanged).
  - Replaced the in-place `_handle_session_resume` body with `_ensure_emitter(websocket, state)` + `state.emitter.emit_session_state()` — the initial session-state on resume now routes through the emitter so `current_pipeline` mirrors the live state instead of being hard-coded `None`.
  - Added `_ensure_emitter(websocket, state)` — binds a per-session `PipelineEmitter` whose sink is `websocket.send` (one envelope per wire frame).
  - Added `_invoke_tool_via_emitter(websocket, state, tool_name, params)` — the M4 tool-call site: opens a pipeline, calls `state.emitter.emit_tool_call(...)` around `TOOL_REGISTRY[name].fn(**params)`, closes the pipeline in `finally`. Unknown tool names short-circuit to `_send_error("TOOL_NOT_FOUND", ...)`.
  - Added `_parse_invoke_directive(text)` — recognizes `/invoke <tool_name> <json-params>` user-message bodies as the M4 live-evidence path. Non-directive user-messages still stream through the M1 Gemini reply path unchanged.
  - The `user-message` dispatch arm now branches: directive → `_invoke_tool_via_emitter` task; else → `_stream_gemini_reply` task. Both write to `state.inflight_task` so the M1 cancel chain (`asyncio.wait_for(state.inflight_task, timeout=5.0)`) propagates `asyncio.CancelledError` into the emitter's `mark_cancelled` branch.

- `services/agent/tests/test_pipeline_emitter.py` (NEW, ~330 lines, 11 tests):
  - `test_happy_path_state_transitions` — pending → running → complete emits 3 frames.
  - `test_replace_not_reconcile_full_snapshot` — multi-step pipeline carries the full steps list in every frame.
  - `test_error_path_failed_step_carries_code_and_message` — `mark_failed` populates `error_code` + 512-truncated `error_message`; the registry records the code.
  - `test_mark_failed_rejects_malformed_error_code` — lowercase codes pass `mark_failed` (wire-level A.7 emission) but fail at `current_snapshot()` because the D.6 `PipelineStepSummary._validate_error_code_shape` regex catches them.
  - `test_loaded_layers_accumulation_via_layer_uri_return` — `add_loaded_layer` grows the list and emits `session-state`.
  - `test_emit_tool_call_layer_uri_return_funnels_to_loaded_layers` — end-to-end: a tool returning `LayerURI` emits in the order pending / running / session-state / complete.
  - `test_current_pipeline_set_and_cleared` — `session-state.current_pipeline` is non-null while running, `None` after `close_pipeline` (cross-envelope predicate from job-0026).
  - `test_cancel_propagation_emits_cancelled_state` — `asyncio.CancelledError` inside the wrapped tool flips the step to `cancelled` (Invariant 8).
  - `test_error_classifier_buckets_known_exception_types` — `ConnectionError → UPSTREAM_API_ERROR` end-to-end through `emit_tool_call`.
  - `test_loaded_layers_dedup_by_uri` — re-fetch replaces in place.
  - `test_no_merge_helper_exists` — structural A.7 guard: scans `dir(PipelineEmitter)` for `merge`/`apply_delta`/`update_partial`/`reconcile` and fails if any appear.

- `reports/inflight/job-0035-agent-20260606/evidence/capture_live_ws_transcript.py` (NEW, ~280 lines): live-evidence harness. Stubs `agent_adapter.load_settings`/`build_client`/`stream_reply` (so no GCP ADC is needed — the `/invoke` directive path doesn't touch Gemini); registers three demo M4-style tools at runtime (`demo_geocode` returns a dict, `demo_fetch_dem`/`demo_fetch_pop` return `LayerURI`s) using the import-time `@register_tool` pattern job-0032 established (registration is from a harness script, not by editing FROZEN `tools/`); boots the real `_make_handler` WS server on a free port; connects a real `websockets.connect` client; sends `session-resume` + three `/invoke` user-messages; captures every inbound frame to `ws_transcript.json` + `ws_transcript.txt`.
- `reports/inflight/job-0035-agent-20260606/evidence/ws_transcript.json` (NEW) + `ws_transcript.txt` (NEW) — 13 frames captured (4 `session-state` + 9 `pipeline-state`).

## Decisions Made

- **Decision: per-tool `progress_percent` is opt-in via direct `update_progress` calls from the tool body (not via a yielded progress callback).**
  - Rationale: M4 atomic tools are sub-second — they should leave `progress_percent` `None` (D.6 + Invariant 1: workflow-attributed). M5+ solvers will opt-in by `await emitter.update_progress(step_id, ...)` between solver chunks. A yielded-callback shape (like Python's generators) would force every M4 tool to be a coroutine just to pass-through `None`, which is friction without value. Direct method calls match the existing async surface.
  - Alternatives: (1) yielded-callback (`async for progress in invoke()`) — rejected, forces every tool into generator shape. (2) global progress channel — rejected, breaks per-session encapsulation. Surfaced as **OQ-35-PROGRESS-OPT-IN**.

- **Decision: emit `pipeline-state` on every step state transition (not batched / interval-throttled).**
  - Rationale: M4 atomic tools produce 3 frames per tool (pending / running / complete). Worst-case frame volume for the Fort Myers demo is ~15 frames across the whole pipeline — well under any rate-limit. Batching adds latency (user-visible lag on the PipelineStrip) and a coalescing bug surface. The kickoff explicitly TENTATIVE-recommends every-transition for M4; M5+ can revisit when long-running solvers push frame counts up.
  - Alternatives: (1) 100ms coalescing window — rejected, premature. (2) opt-in batching via emitter ctor flag — rejected, same hazard. Surfaced as **OQ-35-EMISSION-FREQUENCY**.

- **Decision: error-code registry is open-set + module-level `EMITTER_ERROR_CODES`, with classifier mapping common exception types to seeded codes.**
  - Rationale: Appendix A.6 is open per Decision G; a closed `Literal` would force a schema bump every time a new tool fails differently. The classifier (`_classify_exception`) is deliberately conservative — anything unknown surfaces as `INTERNAL_ERROR` so we never fabricate a more specific code than the exception type warrants. The registry is a passive set so new codes register via `EMITTER_ERROR_CODES.register("NEW_CODE")` without a schema round-trip; the D.6 `PipelineStepSummary._validate_error_code_shape` regex (job-0030) catches malformed codes at serialization time.
  - Alternatives: (1) closed `Literal[...]` — rejected, premature. (2) no registry — rejected, makes "what codes does this milestone emit" impossible to enumerate for the audit. Routes to: schema (closed-set decision deferred to M6 per job-0030 OQ-30-ERROR-CODE-CLOSED-LITERAL).

- **Decision: `loaded_layers` dedup by `uri` field (in-place replacement on re-fetch).**
  - Rationale: TENTATIVE per kickoff. Re-fetch typically means the upstream metadata refreshed (style_preset adjusted, units changed) — preserving a single entry per logical layer is what the LayerPanel expects (one row per layer, not one per fetch). Dedup by `layer_id` would be more name-stable but `layer_id` is generated per-fetch by some tools (`f"dem_{new_ulid()[:8]}"` in the demo), so it would never dedup. The `uri` is content-addressed in the cache substrate (job-0032), which makes it the right key. Documented in the emitter docstring. Surfaced as **OQ-35-LOADED-LAYERS-DEDUP**.

- **Decision: `/invoke <tool> <json>` directive on `user-message` is the M4 live-evidence path, NOT a permanent debug surface.**
  - Rationale: The Gemini-side function-calling integration (which would auto-route Gemini's tool-call output to `TOOL_REGISTRY[name].fn(...)`) is M4 follow-up — but the emission seam must land NOW so job-0036's M4 acceptance has a real wire path to exercise. The directive parser is intentionally narrow (single-tool, JSON params, no chaining) and documented as not in Appendix A. When Gemini function-calling lands the directive arm is replaced by the function-call arm; the underlying `_invoke_tool_via_emitter` helper stays. Surfaced as **OQ-35-INVOKE-DIRECTIVE-LIFETIME**.

- **Decision: keep the M1 `current_pipeline_id` / `current_pipeline_steps` fields on `SessionState` alongside the new `emitter` attribute.**
  - Rationale: the M1 `_stream_gemini_reply` path is FROZEN (kickoff scopes server.py edits to "the tool-call site integration only — do not refactor unrelated WS code"). The Gemini reply path doesn't go through the emitter (it isn't a tool call). Replacing the M1 fields with an emitter dependency would require refactoring the LLM-stream path, which is out of scope. The duplication is small and self-documenting.

- **Decision: don't edit `main.py`.**
  - Rationale: the emitter is wired through `server.py` only — no startup-time DI is required (the emitter is bound per session, not at process start). Avoids the merge-conflict surface called out in the kickoff (jobs 0033/0034 are both editing `main.py` for fetcher / qgis_discovery import lines).

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** Every emission is built from deterministic state transitions; no LLM is in the emission path. `progress_percent` is workflow-attributed (passed in by the caller), never an LLM estimate — the docstring + tests reinforce this. The wire envelope is constructed via `grace2_contracts.ws.Envelope` (M1 invariant: never hand-roll JSON).

- **Invariant 8 (Cancellation is first-class): extends.** The M1 `inflight_task.cancel()` chain propagates `asyncio.CancelledError` into `emit_tool_call`, which calls `mark_cancelled(step_id)` before re-raising. The cancelled step persists in the snapshot; a fresh `pipeline-state` is emitted with the step's `state == "cancelled"` (yellow chip per FR-WC-8), distinct from `failed` (red chip). The cancellation path adds NO new mechanism — it reuses the M1 chain.

- **Appendix A.7 replace-not-reconcile: preserves + structurally enforces.** Every `_emit_*` call serializes the FULL current snapshot. The emitter exposes NO merge / apply_delta / update_partial / reconcile method (`test_no_merge_helper_exists` is the structural guard). The wire ordering (`replace-not-reconcile`) shows in the live transcript: each `pipeline-state` carries the complete step list at that moment.

- **FR-CE-8 / D.6 field discipline (job-0030): preserves.** `progress_percent` is `None` on M4 atomic tool steps (the demo tools don't call `update_progress`). `error_code` + `error_message` populated only when `mark_failed` fires. The 512-char cap on `error_message` is truncated defensively at the emitter (`_truncate_message`) AND enforced by the `PipelineStepSummary` schema (job-0030's `Field(max_length=512)`).

- **Invariant 9 (No cost theater): preserves.** No cost / dollar / duration-estimate fields anywhere in the emitter, the wire envelopes, or the persisted snapshot. `extra="forbid"` on every grace2_contracts model would reject a sneak-in.

## Open Questions

- **OQ-35-PROGRESS-OPT-IN (TENTATIVE: direct `update_progress` calls).** For M4 atomic tools the field stays `None`. For M5+ solver dispatch the recommended shape is `await emitter.update_progress(step_id, percent)` between solver chunks; this avoids forcing every M4 tool into a generator shape just to pass-through `None`. Alternative (yielded-callback) was considered and rejected. Routes to: agent (M5+ solver dispatch job).

- **OQ-35-EMISSION-FREQUENCY (TENTATIVE: every transition for M4).** M4 atomic tools produce ~15 frames per Fort Myers demo run — comfortable. M5+ long-running solvers with 60s+ runtimes calling `update_progress` every 5% may want a 100-250ms coalescing window. Recommend revisit at first M5+ solver landing. Routes to: agent / testing (M4 acceptance and M5+ solver dispatch).

- **OQ-35-ERROR-CODE-REGISTRY-CLOSED-LITERAL (TENTATIVE: open through M5+).** Matches job-0030 OQ-30-ERROR-CODE-CLOSED-LITERAL. The seven seeded codes cover the M4 substrate; new codes register via `EMITTER_ERROR_CODES.register(...)`. A closed `Literal[...]` at M6 would give the web client a known switch surface. Routes to: schema.

- **OQ-35-LOADED-LAYERS-DEDUP (TENTATIVE: dedup by `uri`).** Documented in the emitter docstring. Alternative (dedup by `layer_id`) was rejected because demo tools (and job-0033 fetchers) generate per-fetch `layer_id`s. If a future tool produces stable `layer_id`s a switch could be revisited. Routes to: web (LayerPanel expectations).

- **OQ-35-INVOKE-DIRECTIVE-LIFETIME (TENTATIVE: M4 only).** The `/invoke <tool> <json>` user-message directive exists ONLY because Gemini-side function-calling integration is M4 follow-up. When Gemini's function-call output is wired into `_invoke_tool_via_emitter`, the directive parser is removed. NOT documented as an Appendix A message type. Routes to: agent (next agent job).

- **OQ-35-DEV-INJECTION-SEAM-RETIREMENT (TENTATIVE: keep `import.meta.env.DEV`-gated, do not remove).** Per kickoff and per job-0026 the M3 `window.__grace2InjectPipelineState` dev seam stays useful for local web-client development without standing up the agent. Recommend keeping it gated indefinitely. Routes to: web (job-0036 acceptance follow-up).

- **OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY.** The Appendix A.4 `PipelineStep` (wire shape) carries `progress_percent` but NOT `error_code` / `error_message` — those live only on the persisted `PipelineStepSummary` (D.6) which the client receives via `session-state.current_pipeline`. This is correct per the current Appendix A shapes but creates a subtle asymmetry: a `pipeline-state` envelope with a `failed` step does NOT carry the error context; the client must look at the next `session-state` for the explanation. Recommend a small Appendix A amendment in M5 to add `error_code` / `error_message` to the wire `PipelineStep` so the client doesn't need cross-envelope correlation for error rendering. Routes to: schema.

## Dependencies and Impacts

- **Depends on:**
  - **job-0030-schema-20260606 (APPROVED).** Consumes the three new `PipelineStepSummary` fields (`progress_percent`, `error_code` SCREAMING_SNAKE_CASE, `error_message` 512-char cap). The D.6 `PipelineSnapshot` is the canonical persisted shape `current_snapshot()` returns; `current_pipeline` on `session-state` carries the same shape.
  - **job-0032-agent-20260606 (APPROVED).** Imports `TOOL_REGISTRY` from `grace2_agent.tools`; the import-time `@register_tool` pattern is reused by the live-evidence harness to register three demo tools without touching FROZEN `tools/`. The OQ-32-FROZEN-SERVER-WS-NAME resolution (the M1 WebSocket module is `server.py`) is the file my integration edits live in.
  - **job-0015-agent-20260605 (M1 cancel chain + WebSocket server).** Reuses `inflight_task.cancel()` end-to-end — the emitter's `mark_cancelled` is called from inside the wrapper's `asyncio.CancelledError` branch, before re-raising up the M1 chain.
  - **job-0026-web-20260606 (cross-envelope predicate consumer).** The `session-state.current_pipeline` field this job populates is exactly the cross-envelope predicate-b feeder job-0026's `PipelineStrip` watches. Verified in the live transcript: frames 06 / 10 carry `current_pipeline` non-null while a pipeline runs; frame 12 (after `close_pipeline`) carries `current_pipeline=None`.

- **Affects (downstream consumers):**
  - **job-0036 (M4 acceptance + Fort Myers demo).** Closes **OQ-T-28-SIM-WS-BOUNDARY**: the M3 dev-injection seam (`window.__grace2InjectPipelineState`) is no longer the only path to a `pipeline-state` envelope on the wire. Job-0036's tests can be rewritten to drive the agent (`/invoke` directive or real Gemini function-calling) and verify the rendered `PipelineStrip` matches the agent-emitted snapshot. The live transcript in `evidence/` is the existence proof.
  - **Agent-service follow-up (Gemini function-calling integration).** The Gemini-side ADK function-call output replaces the `/invoke` directive parser; the underlying `_invoke_tool_via_emitter` helper stays. No emitter change required.
  - **Engine workflows (M5+ solver dispatch).** Solver workflows opt-in to `update_progress` between chunks; the emitter API is ready.
  - **job-0033 (data-fetch atomic tools).** The emitter's `_classify_exception` already buckets `ConnectionError → UPSTREAM_API_ERROR` and `ValueError with "bbox"` → `BBOX_INVALID`. When job-0033's fetchers raise these, the failed step gets a populated `error_code` automatically.

## Verification

- **Tests run:**
  - `services/agent/tests/test_pipeline_emitter.py`: **11 passed in 0.04s** (the 11 emitter tests).
  - Full agent suite: `.venv-agent/bin/python -m pytest services/agent/tests/ -q` → **69 passed in 1.35s** (24 from job-0032 baseline + 34 from job-0033 in-flight + 11 new emitter tests; no regressions from this job's edits).
  - Contracts no-regression: `.venv-agent/bin/python -m pytest packages/contracts/ -q` → **131 passed in 0.29s** (unchanged from job-0030 baseline).

- **Live E2E evidence — WS frame transcript (`evidence/ws_transcript.txt`):**

  Captured 13 frames over a real `websockets.connect` round-trip on `ws://127.0.0.1:<free-port>`. Histogram: 4 `session-state` + 9 `pipeline-state`. Frame sequence demonstrates:

  ```
  [00] session-state loaded_layers=[] current_pipeline=None
  [01] pipeline-state steps=['demo_geocode=pending']
  [02] pipeline-state steps=['demo_geocode=running']
  [03] pipeline-state steps=['demo_geocode=complete']
  [04] pipeline-state steps=['demo_fetch_dem=pending']
  [05] pipeline-state steps=['demo_fetch_dem=running']
  [06] session-state loaded_layers=['gs://.../usgs-3dep/demo-dem.tif']
                     current_pipeline=pipeline_id=01KTG39A9EYMG6JYBB12GY4A9H ...
  [07] pipeline-state steps=['demo_fetch_dem=complete']
  [08] pipeline-state steps=['demo_fetch_pop=pending']
  [09] pipeline-state steps=['demo_fetch_pop=running']
  [10] session-state loaded_layers=['gs://.../usgs-3dep/demo-dem.tif',
                                     'gs://.../worldpop/demo-pop.tif']
                     current_pipeline=pipeline_id=01KTG39A9FZBWAVMPYGYPM3S8S ...
  [11] pipeline-state steps=['demo_fetch_pop=complete']
  [12] session-state loaded_layers=[..., ...]  current_pipeline=None
  ```

  All bullet points from the kickoff acceptance #6 satisfied:
  - Initial `session-state` (frame 00).
  - ≥ 3 `pipeline-state` snapshots (9 total).
  - Final `session-state` (frame 12) carries the cumulative `loaded_layers` AND `current_pipeline=None` (cross-envelope predicate-b cleared after `close_pipeline`).
  - Emissions came from the REAL agent (`_make_handler` + `_invoke_tool_via_emitter` + `PipelineEmitter`), NOT the `window.__grace2Inject*` dev seam.
  - Each `pipeline-state` carries the full snapshot per A.7 (no deltas).

- **Tool-call site integration grep (kickoff acceptance #2):**

  ```
  $ grep -n "TOOL_REGISTRY\[" services/agent/src/grace2_agent/server.py
  278:    entry = TOOL_REGISTRY[tool_name]
  ```

  The tool-invocation wrapper goes through `state.emitter.emit_tool_call(name=..., tool_name=..., invoke=lambda: entry.fn(**params))`.

- **FROZEN-paths check:** edits scoped to `services/agent/src/grace2_agent/pipeline_emitter.py` (NEW), `services/agent/src/grace2_agent/server.py` (EDIT — tool-call site integration only), `services/agent/tests/test_pipeline_emitter.py` (NEW), `reports/inflight/job-0035-agent-20260606/`. **NO** edits to `services/agent/src/grace2_agent/tools/{__init__,cache,passthroughs,data_fetch,qgis_discovery}.py`, `mcp.py`, `adapter.py`, `main.py`, `__main__.py`, `packages/contracts/**`, `services/workers/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**`. The parallel job-0033 / job-0034 `main.py` edits are visible in `git status` but untouched by this job — if the orchestrator's audit observes a merge during workflow closure it lands cleanly because `main.py` is not in my staged paths.

- **Results:** **pass.**

  Acceptance criteria (kickoff §"Acceptance criteria"):

  1. `PipelineEmitter` class exists with all 6 transition methods; replace-not-reconcile structurally enforced (no `merge`/`update_partial`). **PASS** — `test_no_merge_helper_exists`.
  2. Tool-call site in `server.py` integrates with the emitter. **PASS** — grep above; the live transcript exercises the path end-to-end.
  3. `session-state` emission populates `loaded_layers` from tool-returned `LayerURI`. **PASS** — `test_emit_tool_call_layer_uri_return_funnels_to_loaded_layers`; transcript frame 10.
  4. Error path emits `failed` step with populated `error_code` + `error_message` (open-set SCREAMING_SNAKE_CASE). **PASS** — `test_error_path_failed_step_carries_code_and_message`, `test_error_classifier_buckets_known_exception_types`.
  5. ≥ 6 unit tests + agent suite green; contracts 131/131. **PASS** — 11 emitter tests + 69 agent total + 131 contracts.
  6. Live WS frame transcript in `evidence/` shows real envelopes emitted by the agent (not dev seam). **PASS** — 13 frames captured; harness is a real `websockets.connect` round-trip against `_make_handler`.
  7. OQ-T-28-SIM-WS-BOUNDARY closeable by job-0036. **PASS** — the emission seam is alive; job-0036's M3 tests can be rewritten to drive the real path.
  8. No edits to FROZEN paths. **PASS**.
