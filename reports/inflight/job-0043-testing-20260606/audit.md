# Audit: M5 acceptance — Hurricane Ian / Fort Myers demo end-to-end + Playwright screenshot capture + sprint-07 close

**Job ID:** job-0043-testing-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** testing

**Prerequisites (ALL APPROVED — required):**
- job-0037 (WorldPop default flip)
- job-0038 (OQ-4 HydroMT decision)
- job-0039 (3 new fetcher tools)
- job-0040 (SFINCS substrate)
- job-0041 (run_solver + wait_for_completion)
- job-0042 (model_flood_scenario workflow + NLCD validation gate)
- **job-0044 (NLCD WMS palette encoding hotfix — REAL SFINCS RUNS UNBLOCKED AT INPUT-DATA LAYER)**

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — §3.10 FR-FR (failure recovery — relevant for honest-failure narration), FR-TA-1 (model_flood_scenario workflow), FR-WC-8/9 (PipelineStrip rendering)
- `docs/srs/04-non-functional-requirements.md` — NFR-P-4 (≤15 min for ≤200 km² at 30 m); NFR-R-3 (30s cancel budget)
- `docs/srs/07-milestones.md` — M5 exit criteria
- `docs/srs/F-data-sources-discovery-secrets.md` — §F.1.1 access tier discipline (NLCD now WCS not WMS post-job-0044)
- `docs/decisions/oq-4-hydromt-depth.md` — HydroMT Full reliance contract
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Environment
The full deployed substrate is operational: Cloud Run QGIS Server (CORS-fixed @sha256:57d0f43), PyQGIS worker (@sha256:fffd7e0f), cache bucket (grace-2-hazard-prod-cache), runs bucket (grace-2-hazard-prod-runs), SFINCS Cloud Run Job + Cloud Workflows (job-0040), agent service runnable locally with all 14 tools registered. **Sprint-07's hard-won substrate work means a real flood-modeling demo is finally attemptable end-to-end.**

### Scope

1. **Fort Myers / Hurricane Ian demo end-to-end live test** in `tests/m5/test_fort_myers_hurricane_ian_demo.py`:
   - User-equivalent query via the agent's `/invoke run_model_flood_scenario` directive
   - The full chain: geocode → fetch_dem → fetch_landcover (Tier 2 WCS post-job-0044) → fetch_river_geometry → lookup_precip_return_period (use 100-yr 24-hr 11.9-inch design storm verified live by job-0042) → build_sfincs_model (NLCD validation gate now PASSES) → run_solver → wait_for_completion → postprocess_flood → AssessmentEnvelope Flood subtype
   - **Two acceptable outcomes:**
     - **SUCCESS:** AssessmentEnvelope returned with `flood_depth` LayerURI pointing at a real COG in the runs bucket; layer renders on the web client through QGIS Server; **screenshot captured**. This is the M5 milestone moment.
     - **HONEST FAILURE:** chain runs through to SFINCS dispatch + wait_for_completion; SFINCS itself fails for a different reason (HydroMT deck-building issue, real model setup quirk, etc.); failed envelope returned with typed error code; honestly disclosed in the report. **Capture screenshots of the failed pipeline state for evidence value.**
   - Either outcome is acceptable for M5 closure — the M5 acceptance criterion is "demo end-to-end attempted with honest disclosure of outcome", not "SFINCS must succeed." Substrate verification matters more than the scientific output for sprint-07 close.

2. **Playwright screenshot capture** during the demo run, mirroring job-0036's pattern:
   - Pre-run: idle PipelineStrip + LayerPanel (baseline)
   - Mid-run: PipelineStrip with progress emissions (≥3 steps in flight — proves real progress emission per job-0041's measured 36 emissions over ~3 min)
   - Final state: SUCCESS (rendered flood-depth layer on basemap) OR HONEST FAILURE (failed step with red marker + error_code visible)
   - Capture at 1280×800 in Chromium; commit under `evidence/screenshots/`
   - **IF the demo succeeds, the orchestrator surfaces the rendered-flood-depth screenshot via SendUserFile proactive immediately on STATE flip to ready-for-audit. IF the demo fails honestly, surface the failure-state screenshot with honest disclosure** — failure-state is itself a valuable visual (proves the substrate works even when SFINCS doesn't).

3. **NFR-P-4 timing capture.** Record full-pipeline wall-clock from `/invoke` issuance through to AssessmentEnvelope return (or failure). Honest qualification per testing.md NFR discipline — single-machine measurement from Debian dev box against us-central1.

4. **Cancel test** mirroring job-0036's pattern: submit a real pipeline run, wait 30s, send cancel envelope, verify the WS pipeline-state envelope flips to cancelled within NFR-R-3's 30s budget. Job-0041 already measured 850 ms on the run_solver layer; this is the full-chain extension.

5. **Full regression preservation.** `make test` + `make test-m2` + `make test-m3` + `make test-m4` + new `make test-m5` all green. M5 acceptance target: 2 new test functions (Fort Myers demo + cancel test). Baseline counts after M5:
   - Contracts: 131/131
   - Agent service: 119/119 (post-hotfix)
   - M1: 30 / M2: 7 / M3: 10 / M4: 2 / M5: ≥2 = ~170 invocations green.

6. **Sprint-07 retrospective** in `reports/sprints/sprint-07.md`. Cover: M5 milestone achievement (or honest qualification on the substrate-vs-scientific-output distinction); cost-discipline shift (cite `reports/cost_tracking.json` totals); the **TWO substrate-level wins** — Invariant 8 cancel chain measured at 850 ms (35× under budget) AND Invariant 7 NLCD validation gate fired LIVE in production catching a real silent-wrong-answer mode; OQ carry-forwards for v0.3.17+ housekeeping; sprint-08 scope notes (Mode 1 data-source catalog + FR-FR-3 max-turns cap + ATCF / HydroMT integration for full-fidelity SFINCS demo).

### File ownership (exclusive)

- `tests/m5/` (NEW directory: `__init__.py`, `conftest.py`, `test_fort_myers_hurricane_ian_demo.py`)
- `Makefile` — `test-m5` target add (mirror `test-m4` opt-in pattern)
- `tests/pyproject.toml` — `live_m5` marker if needed
- `reports/inflight/job-0043-testing-20260606/`
- `reports/sprints/sprint-07.md` — populate Retrospective (orchestrator finalizes)

### FROZEN — no edits in this job

- `services/agent/**` (specialists own their code)
- `packages/contracts/**`, `infra/**`, `web/src/**`, `services/workers/**`, `styles/**`
- `docs/srs/**`, `docs/SRS_v0.3.md`
- `reports/complete/**`
- M3 + M4 test files (only M5-side additions)

### Cross-cutting principles in force

- **Invariant 7 + 8 substrate wins** are headline evidence even if SFINCS itself fails — the demo PROVES the safety substrate works regardless of scientific output success.
- **Failure-naming discipline** (testing.md): every assertion attributes to layer (`web client | agent | workflow | atomic tool | cache shim | QGIS Server | SFINCS | Cloud Workflows | network | upstream API`).
- **Diagnose before fix**: if the demo fails, capture the failed step + WS transcript + GCS state before changing test assertions.
- **§F.1.1 access tier discipline**: NLCD is now Tier 2 WCS (post-job-0044); reflect in any tier-related assertions.

### Acceptance criteria (reviewer re-runs)

- [ ] `tests/m5/test_fort_myers_hurricane_ian_demo.py::test_fort_myers_pipeline_end_to_end` runs; **passes whether SFINCS succeeds OR fails honestly** (substrate verification is the criterion).
- [ ] Screenshots committed under `evidence/screenshots/`: baseline + mid-run + final-state (success or failure).
- [ ] NFR-P-4 timing captured with honest qualification (single-machine methodology).
- [ ] Cancel test verifies full-chain cancel within 30s budget.
- [ ] `make test-m5` target added; all five test tiers green.
- [ ] Sprint-07 retrospective drafted with cost telemetry + the two substrate wins.
- [ ] No edits to FROZEN paths.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: HydroMT deck-building succeeds or fails on real Fort Myers inputs (honest disclosure either way); SFINCS NetCDF-to-COG conversion path on success; whether to attempt ATCF Hurricane Ian forcing or stick with design storm for v0.1; Playwright capture timing (poll loop vs fixed sleep).
