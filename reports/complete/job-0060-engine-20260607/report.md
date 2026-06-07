# Report: agent return-type change — `run_model_flood_scenario` returns `LayerURI`

**Job ID:** job-0060-engine-20260607
**Sprint:** sprint-09
**Specialist:** engine
**Task:** Change `run_model_flood_scenario` to return `LayerURI` (single, the flood-depth COG) so the PipelineEmitter auto-emit gate at `pipeline_emitter.py:517` fires `add_loaded_layer` and populates `session-state.loaded_layers`.
**Status:** ready-for-audit

## Summary

Changed the `run_model_flood_scenario` wrapper function in `model_flood_scenario.py` to return a `LayerURI` on success (when `envelope.layers` is non-empty) and fall back to the JSON-dumped dict for failure envelopes (when `envelope.layers` is empty). The `PipelineEmitter.emit_tool_call` gate at `pipeline_emitter.py:517` now fires `add_loaded_layer` on the M5 success path, which appends to `_loaded_layers` and emits a fresh `session-state` envelope per A.7 replace-not-reconcile. Tests 21 and 22 (already committed by job-0063 as forward-references) were verified: all 25 tests in the file pass.

## Changes Made

- **File:** `services/agent/src/grace2_agent/workflows/model_flood_scenario.py`
  - Changed `run_model_flood_scenario` return type annotation from `dict[str, Any]` to `LayerURI | dict[str, Any]`
  - Updated docstring: describes the LayerURI-on-success / dict-on-failure contract
  - Replaced `return envelope.model_dump(mode="json")` with a conditional that:
    - If `envelope.layers` is non-empty: constructs and returns a `LayerURI` from `envelope.layers[0]` (a `ResultLayer` -- field-for-field identical to `LayerURI` by design per `execution.py` docstring)
    - If `envelope.layers` is empty (failure): falls back to `envelope.model_dump(mode="json")` so LLM can narrate the error
  - Inline comment above the new conditional cites `docs/decisions/layer-emission-contract.md` (ADOPTED 2026-06-07)

- **File:** `services/agent/tests/test_model_flood_scenario.py`
  - Tests 21 (`test_run_model_flood_scenario_returns_layer_uri`) and 22 (`test_run_model_flood_scenario_triggers_loaded_layers_emit`) were ALREADY committed by job-0063 as forward-references. No test file changes needed.
  - All 25 tests pass (20 pre-existing + 3 from job-0063 + 2 from job-0060 that were forward-referenced).

- **Files:** `reports/inflight/job-0060-engine-20260607/evidence/`
  - `smoke_demo.py` -- updated harness calling `run_model_flood_scenario` through `emit_tool_call` and capturing emitted frames
  - `smoke_demo_envelope.json` -- live run output (honest failure: `LANDCOVER_READ_FAILED` -- GCS credentials unavailable on this machine; the chain ran through geocode + DEM + landcover fetch + NLCD gate + Atlas 14 forcing, then stopped at GCS read for the landcover raster)
  - `session_state_envelope.json` -- captured session-state wire frame from a mocked happy-path execution (via the same code path as test 22), showing `loaded_layers[0].uri == gs://grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif`

## Decisions Made

- **Decision:** Return `LayerURI` from `envelope.layers[0]` on success; fall back to dict for failure (no layers) -- Option A from the kickoff.
  - **Rationale:** The `PipelineEmitter.emit_tool_call` gate at `pipeline_emitter.py:517` does `isinstance(result, LayerURI)` -- not `isinstance(result, list)`. Option A (single LayerURI) is the minimal correct fit for the existing emitter gate. Option B (list) would require widening the gate, which the kickoff explicitly marks FROZEN.
  - **Alternatives considered:** Option B (return `list[LayerURI]`) -- rejected because the emitter gate only handles `isinstance(result, LayerURI)`, not a list; widening the emitter is FROZEN under this job.

- **Decision:** Fall back to `envelope.model_dump(mode="json")` for failure envelopes (empty layers) rather than raising.
  - **Rationale:** The inner `model_flood_scenario` function returns typed failed envelopes (not raises) for all internal failures per OQ-42-PARTIAL-FAILURE-ENVELOPE-SHAPE. The LLM narrates the error code threaded into `flood.metrics.solver_version`. Raising would suppress the structured error info.

- **Decision:** Construct a new `LayerURI` from `ResultLayer` fields rather than using the `LayerURI` objects from `postprocess_flood` directly.
  - **Rationale:** The inner workflow converts `list[LayerURI]` (from `postprocess_flood`) into `list[ResultLayer]` when building the `AssessmentEnvelope`. Only the `ResultLayer` objects are accessible on `envelope.layers`. Since `ResultLayer` and `LayerURI` are field-for-field identical by design, the construction is trivial and type-safe.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves** -- no LLM call added; the `LayerURI` fields come directly from `envelope.layers[0]` which is a typed `ResultLayer`.
- **Invariant 2 (Deterministic workflows): preserves** -- the wrapper still calls the inner `model_flood_scenario` unchanged; only the return type changes.
- **Invariant 3 (Engine registration, not modification): preserves** -- no agent core change; only the wrapper return type.

## Open Questions

- None. The kickoff explicitly covers both options (A and B) and the emitter gate is clearly documented. Decision log above explains the choice.

## Dependencies and Impacts

- **Depends on:** job-0035 (agent, APPROVED) -- PipelineEmitter substrate; job-0042 (engine, APPROVED) -- `model_flood_scenario` workflow; job-0058 (engine, APPROVED) -- postprocess_flood COG path.
- **Affects:** Web client -- `LayerPanel.tsx` already consumes `session-state.loaded_layers` (layer-emission-contract.md confirms it is already conformant). No web changes needed.

## Verification

- **Tests run:** `services/agent/tests/test_model_flood_scenario.py` -- **25/25 passed**
  - Test 21 (`test_run_model_flood_scenario_returns_layer_uri`): PASS
  - Test 22 (`test_run_model_flood_scenario_triggers_loaded_layers_emit`): PASS
  - All 20 pre-existing tests: PASS
  - Tests 23-25 (job-0063 OQ-59 CRS fix): PASS

- **Live E2E evidence:** `qualified` -- GCS credentials unavailable on this machine; chain ran geocode -> DEM cache hit -> landcover cache hit -> river geometry cache hit -> Atlas 14 cache hit -> NLCD gate (PASS) -> stopped at `LANDCOVER_READ_FAILED`. Result: `dict` return (correct fallback for empty-layers failure envelope). See `evidence/smoke_demo_envelope.json`.

- **Session-state evidence:** `evidence/session_state_envelope.json` -- generated via mocked happy-path execution through `PipelineEmitter.emit_tool_call` (same code path as test 22). Shows `payload.loaded_layers[0].uri == "gs://grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif"`.

- **Results:** pass (tests) + qualified (live run, documented reason)
