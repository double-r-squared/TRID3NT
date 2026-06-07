# Audit: agent return-type change — `run_model_flood_scenario` returns `LayerURI`

**Job ID:** job-0060-engine-20260607, **Sprint:** sprint-09, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- `docs/decisions/layer-emission-contract.md` (ADOPTED 2026-06-07) — `session-state.loaded_layers` is canonical; `map-command` is for transient verbs.
- job-0058 (engine, APPROVED): postprocess_flood squeeze + production COG path closed.
- job-0035 (agent, APPROVED): PipelineEmitter substrate (`pipeline_emitter.py:413-440 add_loaded_layer`, `pipeline_emitter.py:517 isinstance(LayerURI) auto-emit gate`).
- job-0042 (engine, APPROVED): `model_flood_scenario` workflow (current dict-returning surface at `model_flood_scenario.py:647`).

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-MP-3 (source of truth) + FR-TA-1 (workflow return type) + the new FR-MP-6 Case UX (v0.3.21 — informs what `loaded_layers` will eventually feed)
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Why this job exists

Job-0058 produced a real flood-depth COG end-to-end. The web UI does not display it. The smallest change that closes the loop is making the M5 atomic-tool wrapper return a `LayerURI` instead of the JSON-dumped envelope dict, so the existing PipelineEmitter auto-emit branch fires and `session-state.loaded_layers` populates.

Per the layer-emission-contract: declarative > imperative. The `MapCommand.load-layer` envelope is NOT used for layer surfacing; layers flow through `session-state.loaded_layers` (replace-not-reconcile per A.7).

### Scope

1. **`services/agent/src/grace2_agent/workflows/model_flood_scenario.py`** — change `run_model_flood_scenario` (atomic-tool wrapper) so its return type is `LayerURI | list[LayerURI]`, not the JSON-dumped `AssessmentEnvelope`. Implementation choice (whichever is simpler):
   - **Option A:** return `envelope.layers[0]` (the primary `LayerURI` — the flood-depth COG) when the chain succeeds; raise on failure (existing typed errors).
   - **Option B:** return `envelope.layers` (the full list) so multiple layers can land in one call when future workflows produce them.
   - **Either way:** the `AssessmentEnvelope` is still constructed AND emitted via the `tool-call-complete` payload (so chat-message + Mongo persistence paths still work). Only the tool-call return-type changes.

2. **Tests** in `services/agent/tests/test_model_flood_scenario.py`:
   - Add ≥2 tests that assert the return type is `LayerURI` (or `list[LayerURI]`) and that `PipelineEmitter.add_loaded_layer` was invoked during emission (mock or capture the emit).
   - Existing tests must still pass (165/165 baseline).

3. **Verify end-to-end** that on the next live M5 run, the `session-state` envelope emitted post-workflow carries `loaded_layers[0].uri == <new COG gs:// URI>`. Capture a sample `session-state` JSON in `evidence/`.

4. **Document the contract pin** with a short inline comment in `model_flood_scenario.py` linking to `docs/decisions/layer-emission-contract.md` so future readers don't try to revert.

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — `run_model_flood_scenario` body + return type
- `services/agent/tests/test_model_flood_scenario.py` — additive tests
- `reports/inflight/job-0060-engine-20260607/`

### FROZEN
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py` (sprint-8 work; don't disturb)
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` (concurrent job-0063 owns OQ-59 fix)
- `services/agent/src/grace2_agent/pipeline_emitter.py` (the auto-emit branch is correct; don't widen it here)
- `packages/contracts/**` (no envelope schema change required)
- All other workflows/* and tools/* files
- All other paths

### Acceptance criteria
- [ ] `run_model_flood_scenario` returns `LayerURI` (or `list[LayerURI]`)
- [ ] PipelineEmitter `add_loaded_layer` fires on tool-call return (verified via test mock or via live `session-state` capture)
- [ ] All pre-existing tests pass (165+/165+)
- [ ] ≥2 new tests guard the return-type contract
- [ ] Inline comment cites `docs/decisions/layer-emission-contract.md`
- [ ] Live evidence captured: a `session-state` envelope JSON with non-empty `loaded_layers` populated by the M5 run
- [ ] No edits to FROZEN paths
- [ ] Single commit
