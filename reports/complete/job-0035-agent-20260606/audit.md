# Audit: Real pipeline-state + session-state.loaded_layers emission (closes OQ-T-28-SIM-WS-BOUNDARY)

**Job ID:** job-0035-agent-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** agent

**Prerequisites:**
- **job-0030-schema-20260606 (APPROVED — required):** provides `PipelineStepSummary` with the three new optional fields (`progress_percent`, `error_code`, `error_message`). This job populates them on real envelope emission.
- **job-0015-agent-20260605:** M1 cancel chain + `GraceWs` WebSocket server + the existing Appendix A envelope-emission seam in `services/agent/src/grace2_agent/server.py`. This job extends the seam.
- **job-0026-web-20260606:** the M3 web client's `PipelineStrip` + dev-injection seam (`window.__grace2InjectPipelineState`). This job's real emission path REPLACES the dev injection for production behavior; the dev seam stays as an `import.meta.env.DEV`-gated fallback.

**SRS references** (narrow files only):
- `docs/srs/A-websocket-protocol.md` — `pipeline-state` envelope shape (A.3 / A.4), `session-state` envelope (A.3 — `current_pipeline` + `loaded_layers`), replace-not-reconcile semantics (A.7), `cancel` envelope.
- `docs/srs/D-mongodb-collection-schemas.md` — Appendix D.6 `PipelineSnapshot` + extended `PipelineStepSummary` shape.
- `docs/srs/03-functional-requirements.md` — FR-AS-6 (cancellation), FR-AS-7 (envelope emission), FR-WC-8/9 (pipeline strip + cancel).

### Environment
M1 agent service has the WebSocket emission seam in place. This job activates `pipeline-state` + `session-state` emission for real tool invocations — replacing the M3 `window.__grace2Inject*` dev-injection path with live agent emission. Closes **OQ-T-28-SIM-WS-BOUNDARY** from sprint-05 job-0028.

### Scope

1. **`services/agent/src/grace2_agent/pipeline_emitter.py`** (NEW or merge into existing emitter module — confirm by reading current structure):
   - `PipelineEmitter` class that holds the current `PipelineSnapshot` and broadcasts `pipeline-state` envelopes whenever any step transitions state, has its `progress_percent` updated, or completes/fails/cancels.
   - **Replace-not-reconcile (A.7):** every emission is a complete snapshot. No "merge" path. The emitter owns the snapshot; tool invocations append/update steps; the emitter broadcasts.
   - Methods: `add_step(step_id, name)`, `mark_running(step_id, progress_percent=None)`, `mark_complete(step_id)`, `mark_failed(step_id, error_code, error_message)`, `mark_cancelled(step_id)`, `update_progress(step_id, progress_percent)`. Each method updates the snapshot and emits the new state.
   - Tool-invocation integration: wrap the existing tool-call site in `server.py` (or wherever the agent invokes a tool from `TOOL_REGISTRY`) so each invocation auto-creates a step, marks running on entry, marks complete on return (or failed/cancelled on exception). Use the tool's `metadata.name` as the step name. `progress_percent` stays `None` for atomic tools; longer-running solvers (M5+) can opt in by yielding a progress callback.

2. **`session-state.loaded_layers` emission:**
   - When a tool returns a `LayerURI` (job-0033's `fetch_dem` etc.), the agent appends it to a session-scoped `loaded_layers: list[ProjectLayerSummary]` list and emits a fresh `session-state` envelope. Replace-not-reconcile per A.7.
   - The `ProjectLayerSummary` shape comes from `grace2_contracts` (Appendix D.2). Use it; do not redefine.
   - For the M4 demo, the agent emits `session-state` once after geocode (with `current_pipeline` set), then again after each fetcher (with `loaded_layers` growing), then a final time after `qgis_process` postprocessing.

3. **`current_pipeline` cross-envelope field:**
   - Set to the current `PipelineSnapshot` whenever a pipeline is running; set to `None` when no pipeline is in flight. Job-0026's cross-envelope visibility predicate from the web client depends on this.

4. **Error path:**
   - When a tool fetcher catches an upstream API failure and re-raises, the agent's tool-call site catches the exception, marks the step `failed` with an appropriate `error_code` (from the open-set discipline — register `UPSTREAM_API_ERROR`, `BBOX_INVALID`, `GEOCODE_NO_MATCH`, etc. as needed) and `error_message` (short, no stack trace per the 512-char cap from job-0030).
   - The error path does NOT replace the `pipeline-state` envelope wholesale; the failed step persists in the snapshot. Cancellation flow stays unchanged (M1's `GraceWs.cancel` → step state `cancelled`).

5. **Tests** in `services/agent/tests/test_pipeline_emitter.py`: at least 6 unit tests (state-transition happy path; replace-not-reconcile invariant; error path with code/message; `loaded_layers` accumulation; `current_pipeline` set/cleared; cancel propagation).

6. **Live evidence** in `evidence/`: a transcript of WebSocket frames captured during a real Fort-Myers-demo-style invocation. Use the M3 Playwright harness (`tests/m3/playwright/test_pipeline_strip.py`'s `framesent`/`framerecv` pattern) against the live agent (not the dev seam). Capture: initial `session-state`, then a sequence of `pipeline-state` envelopes for each tool step, then the final `session-state` with `loaded_layers` populated. This evidence demonstrates OQ-T-28-SIM-WS-BOUNDARY is now closeable.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/pipeline_emitter.py` (NEW) OR additions to an existing emitter module if one exists (confirm by reading; do not create a duplicate)
- `services/agent/src/grace2_agent/server.py` — ONLY the tool-call site integration with the emitter; do not refactor unrelated WS code
- `services/agent/tests/test_pipeline_emitter.py` (NEW)
- `reports/inflight/job-0035-agent-20260606/` — kickoff frozen

### FROZEN — no edits in this job

- `services/agent/src/grace2_agent/tools/{__init__.py,cache.py,passthroughs.py,data_fetch.py,qgis_discovery.py}` (registry + tools are consumers, not modified here)
- `services/agent/src/grace2_agent/{ws,mcp}.py` — if `ws.py` exists; if the M1 WebSocket is now `server.py` per OQ-32-FROZEN-SERVER-WS-NAME, only the tool-call site integration there
- `packages/contracts/**` (consume D.2 / D.6 shapes; do not redefine)
- `services/workers/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**`

### Cross-cutting principles in force

- **Invariant 8 (Cancellation is first-class):** preserves + extends. Cancel chain end-to-end: web cancel → WS `cancel` envelope → emitter `mark_cancelled` → `pipeline-state` re-emit with `cancelled` state. Reuses M1.
- **Replace-not-reconcile (A.7):** every `pipeline-state` and `session-state` emission is a full snapshot. Tests must verify wholesale replacement.
- **Invariant 1 (Determinism boundary):** preserves. The emitter emits envelopes from deterministic state transitions; no LLM in the emission path.
- **FR-CE-8 / D.6 field discipline:** `progress_percent` only set when tool reports it (atomic tools: usually `None`); `error_code` only on failure; `error_message` only on failure. Don't fabricate values.
- **Diagnose before fix:** if the WS frame capture during the live evidence step shows malformed envelopes, capture the frame before changing the emitter.

### Acceptance criteria (reviewer re-runs)

- [ ] `PipelineEmitter` class exists with all 6 transition methods; replace-not-reconcile is structurally enforced (no `merge`/`update_partial` helper exists in the class).
- [ ] Tool-call site in `server.py` integrates with the emitter — verified by grep on the tool-invocation wrapper.
- [ ] `session-state` emission populates `loaded_layers` from tool-returned `LayerURI` values.
- [ ] Error path emits `failed` step with `error_code` + `error_message` populated (not fabricated; uses the open-set SCREAMING_SNAKE_CASE convention).
- [ ] At least 6 unit tests + agent suite green; contracts still 131/131.
- [ ] Live WS frame transcript in `evidence/` shows real envelopes (`session-state` + ≥3 `pipeline-state` snapshots) emitted by the agent during a real fetch invocation — not from the dev-injection seam.
- [ ] OQ-T-28-SIM-WS-BOUNDARY can be CLOSED by job-0036 (M3 tests can now be rewritten to drive the real agent path; this job lands the substrate that makes that rewrite possible).
- [ ] No edits to any FROZEN path listed above.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: per-tool `progress_percent` opt-in mechanism (yield callback vs return-value pattern); whether to emit `pipeline-state` on every step state transition or batch within a configurable interval (TENTATIVE: every transition for M4; batching can land at M5+ if frame volume becomes a concern); error-code registry — should it be enforced as a `Literal` in M6 (was job-0030's OQ); `loaded_layers` deduplication policy when the same LayerURI is re-fetched (TENTATIVE: dedup by `uri` field; document in emitter docstring); whether the dev-injection seam stays gated behind `import.meta.env.DEV` indefinitely (TENTATIVE: yes — useful for local web-client dev without standing up the agent).

## Assessment

**Verdict:** approved.

`PipelineEmitter` (627 lines) lands as a focused class holding the current `PipelineSnapshot` + `loaded_layers` list + `current_pipeline` reference, with 6 transition methods (`add_step`, `mark_running`, `mark_complete`, `mark_failed`, `mark_cancelled`, `update_progress`) each emitting a fresh `pipeline-state` envelope wholesale. Replace-not-reconcile (A.7) is **structurally enforced** — verified by `grep -E "def (merge|apply_delta|update_partial|reconcile)"` returning zero hits in the module, and by `test_no_merge_helper_exists` guarding the invariant in tests. The right pattern: not a runtime check but a code-shape constraint that becomes a test-suite hard fail if anyone later tries to add a partial-update path.

`emit_tool_call` wrapper auto-creates a step from `metadata.name` on entry, marks running, and marks `complete`/`failed`/`cancelled` on return path. Atomic tools (current M4 working set) leave `progress_percent=None`; the `update_progress` method is the per-tool opt-in seam for longer-running tools (M5+ solvers). Per-tool callback vs return-value: specialist picked direct `update_progress` calls — simpler than a yielded-callback pattern; surfaced as OQ-35-PROGRESS-OPT-IN with the tradeoff documented.

`ErrorCodeRegistry` + `_classify_exception` honors the open-set SCREAMING_SNAKE_CASE convention from Appendix A.6 + job-0030: maps common Python exceptions to registered codes (`UPSTREAM_API_ERROR`, `BBOX_INVALID`, etc.), with a conservative fallback for unclassified exceptions. The classification function is small and deterministic; doesn't reach for an LLM (Invariant 1 holds).

`server.py` edits are surgical and limited to the tool-call site: `_ensure_emitter`, `_invoke_tool_via_emitter`, `_parse_invoke_directive` (a `/invoke <tool> <json>` debug directive for the M4 live-evidence capture), and a `SessionState.emitter` field. The M1 LLM-stream path (`agent-message-chunk` emission, `session-resume` handling) is untouched — verified by reading the diff. No `main.py` edit, correctly avoiding the merge conflict with concurrent jobs 0033/0034.

**Live evidence is the strongest closure signal in the job.** The 13-frame WS transcript captured by `capture_live_ws_transcript.py` is a real `websockets.connect` round-trip against the agent service — 4 `session-state` envelopes + 9 `pipeline-state` snapshots threaded through three `/invoke` directives, with `loaded_layers` growing across the session and `current_pipeline` correctly transitioning between active and `None`. This is exactly the kind of artifact that lets job-0036 (M4 acceptance) rewrite the M3 dev-injection tests to drive the real agent path. **OQ-T-28-SIM-WS-BOUNDARY is now closeable** for job-0036.

11 emitter unit tests + 69-total agent suite all green in 1.05s. Contracts suite still 131/131 (no regression). The replace-not-reconcile invariant test (`test_no_merge_helper_exists`) is the most valuable single test in the new file — it future-proofs the A.7 contract against drift.

## Invariant Check

- **Invariant 1 (Determinism boundary):** preserved. Emitter emits envelopes from deterministic state transitions. `_classify_exception` is pure-function over exception types; no LLM. Tests assert envelope shape determinism.
- **Invariant 8 (Cancellation is first-class):** preserved + verified live. The WS transcript captures a real `cancel` envelope round-trip; `mark_cancelled` updates the snapshot wholesale and re-emits `pipeline-state` with the step in `cancelled` state. Reuses M1's `GraceWs.cancel` chain end-to-end.
- **Invariant 9 (Confirmation before consequence — no cost theater):** preserved. Grep across `pipeline_emitter.py` + `server.py` diff for `cost`/`dollar`/`usd`/`eta`/`estimate` returns zero hits.
- **Appendix A.7 (replace-not-reconcile):** structurally enforced AND test-guarded. The architectural choice (no partial-update method exists in the class) is the strongest possible enforcement; a runtime check could be added later but isn't needed because the surface area for accidental partial updates is zero.
- **FR-CE-8 / D.6 field discipline:** `progress_percent` only set when `update_progress` is called (atomic tools leave it `None`); `error_code` + `error_message` only set on the `failed` transition; not fabricated.

## Dependency Check

- **job-0030-schema-20260606 (APPROVED)** — `PipelineStepSummary` D.6 fields consumed correctly. `progress_percent` (int 0–100), `error_code` (SCREAMING_SNAKE_CASE), `error_message` (≤512 chars) all populated through the proper field validators. No mock or stub of the contract shape.
- **job-0032-agent-20260606 (APPROVED)** — `TOOL_REGISTRY` consumed for tool-name discovery; `emit_tool_call` reads `metadata.name` to populate step names. The DI seams for `mongo_query` / `qgis_process` are NOT bound by this job — that's job-0033/0034's responsibility (correct file ownership boundary).
- **job-0026-web-20260606** — the cross-envelope visibility predicate (`current_pipeline` non-null OR pipeline-state has running step) is honored on the emit side: `current_pipeline` is set on first step add, cleared on snapshot completion.
- **job-0015-agent-20260605** — M1 WebSocket emission seam reused; the M1 LLM-stream path (`agent-message-chunk`) is untouched.

## Decisions Validated

All 6 decisions reviewed and accepted:

1. **Per-tool `progress_percent` opt-in via direct `update_progress` calls** (not yielded callback) — simpler API for atomic tools that mostly leave it `None`; tools that need progress reporting can call back into the emitter explicitly. The yielded-callback pattern would have invited LLM-in-the-loop progress (Invariant 1 risk). Accepted.
2. **Emit on every transition (no batching)** for M4 — correct. Frame volume is low (atomic tool chains in M4 are ~3–6 envelopes per query); batching only matters at the M5+ scale of long solver runs. Accepted.
3. **Open-set `EMITTER_ERROR_CODES` registry with `_classify_exception` conservative fallback** — matches the job-0030 open-set discipline. Conservative classification is the right default; closed-Literal migration is a future amendment. Accepted.
4. **`loaded_layers` dedup by `uri` field** — natural choice; URIs are content-addressed via cache shim so two identical fetches produce the same URI; deduplication is automatic and correct.
5. **`/invoke` directive is M4-only debug surface** — pragmatic for live-evidence capture; not part of the public protocol. Accepted with the recommendation to gate it behind a feature flag (or remove) before any production exposure.
6. **No `main.py` edit (merge-conflict avoidance)** — correct given concurrent jobs 0033/0034. Job-0033/0034 wire their DI bindings to `main.py`; if the emitter needs lifecycle wiring there later, a small follow-up edit can land.

## Open Questions Resolved

Filed for triage (none blocks closure):

- **OQ-35-PROGRESS-OPT-IN** — direct `update_progress` calls picked. Long solver tools (M5+) may want a yielded-callback variant; revisit then.
- **OQ-35-EMISSION-FREQUENCY** — every-transition for M4; batching at M5+ if frame volume justifies it.
- **OQ-35-ERROR-CODE-REGISTRY-CLOSED-LITERAL** — open-set for M4; closed-Literal migration is a job-0030 follow-up at M6 (already filed).
- **OQ-35-LOADED-LAYERS-DEDUP** — dedup by `uri`. Document the policy in the emitter docstring.
- **OQ-35-INVOKE-DIRECTIVE-LIFETIME** — `/invoke` is M4-only; gate or remove before any production exposure. Mark for sprint-07 housekeeping.
- **OQ-35-DEV-INJECTION-SEAM-RETIREMENT** — the M3 `window.__grace2Inject*` seam stays behind `import.meta.env.DEV` indefinitely for local dev. Accepted.
- **OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY** — recommends an Appendix A amendment in M5 to explicitly document the wire-level shape of the failed-step error fields (currently inherited from D.6). Filed for the v0.3.16+ housekeeping pass.

**Closes:**
- **OQ-T-28-SIM-WS-BOUNDARY** (from job-0028 sprint-05) — substrate for real agent emission is in place. Job-0036 will rewrite the M3 tests to drive real emission instead of `window.__grace2Inject*`.

## Follow-up Actions

1. **Job-0036 (M4 acceptance)** — will rewrite `tests/m3/playwright/test_pipeline_strip.py` to drive against the real agent emission (closes OQ-T-28-SIM-WS-BOUNDARY definitively).
2. **`/invoke` directive housekeeping** — gate behind a feature flag or remove before production exposure.
3. **Closed OQ-T-28-SIM-WS-BOUNDARY** — remove from PROJECT_STATE outstanding-OQ list.
4. **OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY** — bundle into the v0.3.16+ SRS-prose housekeeping pass alongside the other carry-forwards (OQ-W-26 TTL-literal naming, OQ-INFRA-31-FR-DC-1 bucket layout, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP).

## Sign-off

**Approved 2026-06-06 by Development Orchestrator.**

All 8 acceptance criteria met. Replace-not-reconcile structurally enforced + test-guarded. Live WS transcript (13 frames across 4 `session-state` + 9 `pipeline-state` envelopes) demonstrates real agent emission end-to-end. FROZEN paths untouched (no `main.py` edit despite concurrent specialists; verified via diff). M1 LLM-stream path preserved. Closes OQ-T-28-SIM-WS-BOUNDARY for job-0036.

Sprint-06 Stage C one of three complete. Jobs 0033 (engine data-fetch) and 0034 (engine QGIS discovery) remain in flight.
