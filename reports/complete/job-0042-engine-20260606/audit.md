# Audit: model_flood_scenario workflow (M5 chain composition — closes OQ-36-QGIS-PROCESS-DEMO-CHAIN)

**Job ID:** job-0042-engine-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0033, 0037, 0039 (APPROVED):** all 8 needed atomic-tool inputs are live (fetch_dem, fetch_landcover with NLCD vintage_year sidecar, fetch_river_geometry, fetch_population, lookup_precip_return_period, geocode_location, run_solver, wait_for_completion). Read job-0039's report end-to-end (the live-verification discoveries — NLCD Tier 2 WMS GetMap — affect how this workflow consumes landcover).
- **job-0038 (APPROVED) `docs/decisions/oq-4-hydromt-depth.md`:** **THE BINDING CONTRACT** for this job. §4 "Immediate (job-0042)" defines `build_sfincs_model(dem_uri, landcover_uri, forcing, bbox, options) → ModelSetup` wraps `SfincsModel` via Full HydroMT; **MUST implement the NLCD validation gate** raising `SFINCSSetupError("LULC_MAPPING_MISMATCH")` before HydroMT's roughness component runs silently with bad defaults. **This is the Invariant 7 mitigation.**
- **job-0040 (APPROVED):** SFINCS Cloud Workflows + Cloud Run Job substrate is the dispatch target. `run_solver(solver="sfincs", model_setup_uri=..., compute_class=...)` from job-0041 is your composition seam.
- **job-0041 (APPROVED):** `run_solver` + `wait_for_completion` are how you dispatch + observe progress. 850 ms cancel chain measured live.
- **job-0035:** PipelineEmitter is what `wait_for_completion` emits through; the workflow inherits Invariant 8 cancel chain via the existing seams.

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — FR-TA-1 (workflow shape), FR-TA-2 (atomic-tool composition), FR-AS-7 (envelope emission discipline), FR-CE-1/2/3/6/7 (Cloud Workflows orchestration + precondition + cancellation conformance)
- `docs/srs/B-assessment-envelope-schema.md` — Appendix B.2 `AssessmentEnvelope`, B.3 supporting types, B.4 Flood subtype shape (Modeled flood scenario)
- `docs/srs/02-system-overview.md` — Decision G (two-layer architecture)
- `docs/decisions/oq-4-hydromt-depth.md` — binding contract for `build_sfincs_model`
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Environment
This is the M5 capstone composition before testing. The workflow composes 8 atomic tools landed across sprint-04 through sprint-07 into a single deterministic chain that returns an `AssessmentEnvelope` Flood subtype (Appendix B.4) carrying a real flood-depth COG `LayerURI` from a real SFINCS run on the deployed substrate.

### Scope

1. **`services/agent/src/grace2_agent/workflows/__init__.py`** (NEW package) — establishes the workflows package convention. Workflows are NOT atomic tools (don't use `@register_tool`); they're orchestrator-style Python functions that compose atomic tools deterministically per Decision G + FR-TA-1.

2. **`services/agent/src/grace2_agent/workflows/model_flood_scenario.py`** (NEW) — implement:
   ```python
   def model_flood_scenario(
       bbox: tuple[float, float, float, float] | None = None,
       location_query: str | None = None,
       event_id: str | None = None,
       return_period_yr: int = 100,
       duration_hr: int = 24,
       compute_class: str = "medium",
   ) -> AssessmentEnvelope:
       """Compose the full M5 flood-modeling chain.

       Resolves location (geocode if bbox not given) → fetches DEM (3DEP) +
       landcover (NLCD) + river geometry (NHDPlus HR) + precip return period
       (NOAA Atlas 14) → builds SFINCS model via HydroMT (validates NLCD
       vintage against Manning's mapping CSV per OQ-4 Invariant 7
       requirement; raises SFINCSSetupError on mismatch) → dispatches
       run_solver(sfincs) → wait_for_completion (with real progress
       emission through PipelineEmitter) → postprocesses → returns
       AssessmentEnvelope Flood subtype.
       """
   ```

3. **`services/agent/src/grace2_agent/workflows/sfincs_builder.py`** (NEW) — implement `build_sfincs_model(dem_uri, landcover_uri, river_geometry_uri, forcing, bbox, options) → ModelSetup` per the OQ-4 decision §4. **MUST** include:
   - HydroMT data-catalog bridging layer (GCS-backed via `fsspec[gcs]` per OQ-4 §4 contract)
   - **NLCD vintage validation gate**: read the `nlcd_vintage_year` from the landcover LayerURI's sidecar dict; load the version-pinned `manning_mapping.csv` (which you must also author and commit); check the fetched NLCD vintage's class set is a subset of the mapping CSV; raise `SFINCSSetupError("LULC_MAPPING_MISMATCH", details={...})` on mismatch
   - Programmatic YAML build config generation (not user-input)
   - HydroMT call invocation; on success returns `ModelSetup{model_setup_uri: gs://...}` pointing at the GCS-uploaded SFINCS deck
   - The `hydromt-sfincs >= 1.1.2, < 2.0` dependency pin (job-0040's container needs this; surface as schema-pushback to job-0040 if not bundled)

4. **`services/agent/src/grace2_agent/workflows/manning_mapping.csv`** (NEW) — author the NLCD-integer → Manning's-value mapping. Cite a source (HydroMT-SFINCS default + Liu & DeGroote 2010 wetlands adjustments, or whatever the canonical reference is for this CSV). Pin version in a comment block. **This file is the OQ-4 Invariant 7 substrate** — if the CSV is wrong or stale, the validation gate is the safety net.

5. **`services/agent/src/grace2_agent/workflows/postprocess_flood.py`** (NEW or merged into model_flood_scenario.py) — implement `postprocess_flood(run_outputs_uri) → list[LayerURI]` that reads the SFINCS NetCDF output (flood depth at peak), converts to a Cloud-Optimized GeoTIFF written to `gs://grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif`, returns a `LayerURI` pointing at it. Style preset: `continuous_flood_depth` (introduces a new QML preset — if styles/ is FROZEN under engine, surface as styles-side follow-up).

6. **Wire the workflow into the agent service.** Workflows are exposed to the LLM differently than atomic tools — typically by referencing them in tool docstrings or via a workflow dispatcher. For v0.1, add a thin atomic-tool wrapper `run_model_flood_scenario_workflow(...)` registered with `@register_tool(AtomicToolMetadata(name="run_model_flood_scenario", ttl_class="live-no-cache", cacheable=False, source_class="workflow_dispatch"))`. The atomic wrapper calls the workflow internally; the LLM sees a single tool.

7. **Smoke run live evidence** — invoke the workflow once with the Fort Myers / Hurricane Ian context (NHC ATCF Hurricane Ian track for 2022 if possible; else synthetic 100-year return period storm). Capture:
   - Each tool-call step's `pipeline-state` emission
   - The HydroMT model build success (or NLCD vintage validation gate firing — both are valuable evidence)
   - The dispatched run_solver execution handle
   - The wait_for_completion result
   - The final AssessmentEnvelope shape + the flood-depth COG GCS URI

If SFINCS itself fails on this smoke (no real ATCF integration yet, real model setup quirks), the workflow chain succeeding through to `run_solver` dispatch + `wait_for_completion` returning a SOLVER_FAILED RunResult is still acceptable evidence — proves the composition works; the underlying SFINCS deck-building is its own concern.

8. **Tests** in `services/agent/tests/test_model_flood_scenario.py` (NEW) — at least 8 tests:
   - Happy-path workflow with mocked atomic tools returns AssessmentEnvelope Flood subtype
   - NLCD vintage validation gate raises SFINCSSetupError when mock landcover has unmapped class
   - NLCD vintage validation gate passes when mock landcover is subset of mapping
   - Workflow propagates cancellation from any step (mock raises CancelledError → workflow re-raises after marking step)
   - Workflow returns failed AssessmentEnvelope when run_solver returns SOLVER_FAILED
   - geocode fallback path (location_query given, bbox not)
   - Direct bbox path (bbox given, location_query not)
   - Both given → bbox wins (or other documented precedence)

### File ownership (exclusive)

- `services/agent/src/grace2_agent/workflows/` (NEW package — `__init__.py`, `model_flood_scenario.py`, `sfincs_builder.py`, `manning_mapping.csv`, optionally `postprocess_flood.py`)
- `services/agent/src/grace2_agent/main.py` — ONLY the eager `workflows.model_flood_scenario` import for FR-CE-8 fail-fast registration of the `run_model_flood_scenario` atomic wrapper
- `services/agent/pyproject.toml` — `hydromt >= 1.0`, `hydromt-sfincs >= 1.1.2, < 2.0` runtime deps
- `services/agent/tests/test_model_flood_scenario.py` (NEW)
- `reports/inflight/job-0042-engine-20260606/`

### FROZEN — no edits in this job

- `services/agent/src/grace2_agent/tools/*.py` (consume atomic tools; do NOT modify them)
- `services/agent/src/grace2_agent/{server,mcp,pipeline_emitter}.py`
- `packages/contracts/**` (AssessmentEnvelope shapes are FROZEN — use existing pydantic types)
- `services/workers/sfincs/**` (job-0040's container — schema-pushback if you need the manifest to carry HydroMT-built-deck pointer instead of raw inputs)
- `infra/**`, `web/**`, `styles/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `reports/complete/**`

### Cross-cutting principles in force

- **Invariant 1 (Determinism boundary):** preserved. Workflow is straight-line composition; no LLM in the chain.
- **Invariant 2 (Deterministic workflows):** preserved. Same inputs → byte-identical SFINCS deck per HydroMT determinism (already validated in OQ-4 decision).
- **Invariant 7 (no silent wrong answers / claims have provenance):** **THE HEADLINE FOR THIS JOB.** NLCD vintage validation gate per OQ-4. If a future NLCD reclassification ships with class integers the mapping CSV doesn't cover, the gate fails closed.
- **Invariant 8 (Cancellation is first-class):** preserved via the run_solver / wait_for_completion chain from job-0041 (850 ms verified).
- **FR-AS-7 / FR-CE-2:** workflow is deterministic Python composition, not an LLM-mediated chain.
- **Decision G (two-layer architecture):** this is the workflow layer; atomic tools are the tier underneath.
- **Diagnose before fix:** if the workflow composition fails, capture the failed step's `pipeline-state` emission + tool output before changing logic.

### Acceptance criteria (reviewer re-runs)

- [ ] `workflows/` package + `model_flood_scenario.py` + `sfincs_builder.py` + `manning_mapping.csv` + `__init__.py` all present.
- [ ] `build_sfincs_model` includes the NLCD vintage validation gate (verified by a test that exercises both pass + fail paths).
- [ ] `model_flood_scenario` composes geocode → fetch_dem → fetch_landcover → fetch_river_geometry → lookup_precip_return_period → build_sfincs_model → run_solver → wait_for_completion → postprocess_flood → AssessmentEnvelope. Each step emits pipeline-state per FR-AS-7.
- [ ] `run_model_flood_scenario` atomic-tool wrapper registered; `--startup-only` shows ≥14 tools (M4's 8 + 3 new fetchers + 2 solver + 1 workflow wrapper = 14).
- [ ] Smoke run live evidence captured under `evidence/` (workflow execution + atomic-tool chain transcripts + final envelope / SOLVER_FAILED honestly disclosed).
- [ ] At least 8 unit tests; full agent suite + contracts still green.
- [ ] No edits to FROZEN paths.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: ATCF Hurricane Ian integration (canonical vs synthetic forcing for v0.1 smoke); Manning's mapping CSV source citation; postprocess output format (COG vs FlatGeobuf vs both); workflow-as-atomic-tool wrapper pattern (is this the right exposure shape, or should workflows have a separate registration mechanism); how the workflow handles partial failures (which step failed → which envelope shape).

## Assessment

**Verdict:** approved.

The M5 capstone composition lands cleanly: new `workflows/` package establishes the workflow-layer convention per Decision G; `model_flood_scenario` composes the full 8-tool chain (geocode → fetch_dem → fetch_landcover → fetch_river_geometry → lookup_precip_return_period → build_sfincs_model → run_solver → wait_for_completion → postprocess_flood → AssessmentEnvelope Flood subtype); `run_model_flood_scenario` atomic-tool wrapper registered so the LLM sees a single invocable entry point. 14 tools on startup. 115/115 tests green (+11 new). 131/131 contracts. 17 files / +3090 lines / commit `cca57bd`.

**THE HEADLINE — Invariant 7 NLCD validation gate fired LIVE on real production data.** The gate caught a real silent-wrong-answer mode that OQ-4 §4 specifically demanded mitigation for: MRLC's WMS GetMap endpoint returns palette-encoded GeoTIFF (the raster bytes are palette indices `[1,3,4,5,6,7,9,10,11,13,14,18,20,21]` referring to a colormap table, not the canonical NLCD class integers `[11,12,21-24,31,41-43,51,52,71-74,81,82,90,95]`). Without the gate, HydroMT's roughness component would have silently filled all unmatched class integers with default `manning_land`/`manning_sea` values, producing a Manning's grid that LOOKED right and a flood map that LOOKED right but was scientifically wrong — exactly the failure mode Invariant 7 exists to prevent.

The gate raised `SFINCSSetupError("LULC_MAPPING_MISMATCH", details={...})`, the workflow caught it, and returned a typed failed envelope with `solver_version="failed:LULC_MAPPING_MISMATCH"` carrying honest provenance. No silent broken output. **This is the single strongest signal in sprint-07** — the validation gate doesn't just exist in tests; it's actively catching real upstream surprises.

**Two live smoke run paths captured:**
1. Real workflow execution against `grace-2-hazard-prod`: 4 cache hits + 1 fresh DEM write; Atlas 14 returned 11.9 inches at Fort Myers / 100-yr / 24-hr; the gate fired on the palette-encoding mismatch.
2. The dispatch chain succeeded — workflow → `run_solver` → real Cloud Workflows execution `afd364bd-19d0-47ce-8d88-78009120af84` against `grace-2-sfincs-orchestrator` → `wait_for_completion` polled ~4 min → SOLVER_FAILED (synthetic-manifest expected). Proves composition works end-to-end through to the SFINCS substrate even when the upstream NLCD data has issues.

`manning_mapping.csv` (v1.0.0, 20 canonical L48 classes) authored with a 4-source citation bundle. Workflow-as-atomic-tool-wrapper exposure pattern (single `run_model_flood_scenario` tool) is the right v0.1 shape — gives the LLM a clean entry point while preserving the workflow-vs-atomic-tool distinction.

**One critical follow-up surfaced: OQ-42-NLCD-WMS-PALETTE-ENCODING.** Real SFINCS runs are blocked until this is fixed — the workflow returns honest typed failure but can't actually produce flood maps. **Opening job-0044 as a focused hotfix** before scaffolding M5 acceptance (job-0043).

## Invariant Check

- **Invariant 1 (Determinism boundary):** preserved. Workflow is straight-line Python composition; gate logic is pure-function set-membership check.
- **Invariant 2 (Deterministic workflows):** preserved — same inputs → byte-identical outputs (per HydroMT determinism per OQ-4 decision).
- **Invariant 7 (no silent wrong answers / claims have provenance):** **THE HEADLINE — verified live in production.** The gate caught a real silent-wrong-answer mode. Failed envelopes carry typed error codes. This is the substrate-level Invariant 7 win sprint-07 was designed to produce.
- **Invariant 8 (Cancellation is first-class):** preserved via job-0041's chain (850 ms verified earlier).
- **Decision G (two-layer architecture):** the `workflows/` package establishes the workflow-tier convention cleanly.
- **FR-AS-7 / FR-CE-2:** workflow is deterministic Python; no LLM in the chain.

## Dependency Check

- **All prerequisites consumed correctly** — every approved atomic tool from sprint-04 through sprint-07 plays into the chain.
- **OQ-4 contract honored end-to-end** — `manning_mapping.csv` exists, version-pinned, with cited sources; `build_sfincs_model` reads the `nlcd_vintage_year` sidecar from job-0039's fetcher; validation gate before HydroMT roughness component runs.

## Decisions Validated

All decisions reviewed and accepted: workflow-as-atomic-tool-wrapper exposure (single `run_model_flood_scenario` tool); 4-source citation bundle for Manning's mapping; single COG postprocess output format for v0.1; `failed:<ERROR_CODE>` threading into `solver_version` field for partial-failure envelopes; Atlas 14 design-storm for v0.1 smoke (ATCF Hurricane Ian deferred to v0.2); EPSG:3857 for v0.1 model CRS (UTM auto-selection sprint-08+).

## Open Questions Resolved

**Closes:** OQ-36-QGIS-PROCESS-DEMO-CHAIN (M5 capstone composition lands).

**Files for triage:**
- **OQ-42-NLCD-WMS-PALETTE-ENCODING** — **CRITICAL** — production fix needed before real SFINCS runs can succeed. **Routes to job-0044 (engine, focused hotfix) — opening immediately.**
- **OQ-42-WORKFLOW-EXPOSURE-PATTERN** — thin atomic-tool wrapper is the v0.1 shape; revisit if multi-workflow exposure shapes emerge.
- **OQ-42-MANNING-MAPPING-SOURCE-CITATION** — 4-source bundle is the v1.0.0 substrate; revisit at sprint-09+ NFR review for citation quality audit.
- **OQ-42-POSTPROCESS-FORMAT-SET** — single COG for v0.1; FlatGeobuf vector option deferred.
- **OQ-42-PARTIAL-FAILURE-ENVELOPE-SHAPE** — `failed:<ERROR_CODE>` in `solver_version` honored for v0.1; schema may push back for a typed `failure_reason` field at next contracts amendment.
- **OQ-42-ATCF-HURRICANE-IAN-INTEGRATION** — deferred to v0.2; v0.1 uses Atlas 14 design-storm.
- **OQ-42-MODEL-CRS-AUTO-UTM** — EPSG:3857 v0.1; UTM auto-selection sprint-08+.
- **OQ-42-FLOOD-DEPTH-PRESET-QML** — styles/ FROZEN; engine styles follow-up needed.
- **OQ-42-PROJECT-SESSION-IDS-IN-DIRECT-CALL** — informational.

## Follow-up Actions

1. **Open job-0044 (engine: NLCD WMS palette encoding hotfix)** — Critical blocker for real SFINCS runs. Small focused engine fix. Bumps counter 43 → 44.
2. **Then scaffold + dispatch job-0043 (M5 acceptance)** — depends on 0044 landing the palette fix so the Hurricane Ian / Fort Myers demo can produce a successful AssessmentEnvelope with a real flood-depth layer.
3. **v0.3.17+ housekeeping carry-forward** grows: OQ-42-NLCD-WMS-PALETTE-ENCODING (post-fix prose alignment in §F.1 — NLCD WMS palette quirk recorded as "how to use" metadata).
4. **Screenshot moment** lands with 0043's successful pipeline.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.**

The M5 capstone composition is complete. NLCD validation gate proven live as the Invariant 7 mitigation OQ-4 demanded — caught a real silent-wrong-answer mode in production. Dispatch chain end-to-end through to SFINCS substrate verified. 14 tools registered; 115/115 tests; OQ-36 closed.

Sprint-07 Stage D complete. Mid-sprint job-0044 opens for the palette encoding hotfix, then Stage E (job-0043 M5 acceptance).
