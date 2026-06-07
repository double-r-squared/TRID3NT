# Audit: Real pipeline-state + session-state.loaded_layers emission (closes OQ-T-28-SIM-WS-BOUNDARY)

**Job ID:** job-0035-agent-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** assigned

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
