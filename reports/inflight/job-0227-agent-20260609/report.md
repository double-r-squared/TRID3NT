# Report: MODFLOW Cloud Workflows integration + run_modflow tool binding (sprint-13 Stage 2)

**Job ID:** job-0227-agent-20260609
**Sprint:** sprint-13
**Specialist:** agent
**Status:** ready-for-audit

## Summary
Bound the MODFLOW 6 + MF6-GWT groundwater-contamination engine into the agent service.
Three new modules (deck-build/submit workflow, UCN→plume-COG postprocess, LLM-facing
atomic tool) + surgical registration. Full chain runs end-to-end against a local mf6
6.5.0 binary (GRACE2_MODFLOW_LOCAL=1) to Normal termination, producing a PlumeLayerURI
with max_concentration_mgl=1122.34 mg/L and plume_area_km2=0.0275 km², reprojected to
EPSG:4326 over Fort Myers. Both CRITICAL Stage-1 handoff fixes (gwf/+gwt/ subdir deck
layout + manifest model_crs) implemented and verified against the entrypoint contract.

## Changes Made
- workflows/run_modflow.py (NEW): build_and_stage_modflow_deck (adapter build + subdir
  reorg + manifest with model_crs + GCS stage); submit_modflow_run (Cloud Workflows ->
  ExecutionHandle); run_modflow_local (local mf6 + completion.json); DI seams.
  _reorganize_into_subdirs is the handoff fix: moves gwf_model.*->gwf/, gwt_model.*->gwt/,
  rewrites mfsim.nam model+ims refs AND each model namefile's package refs to the subdir.
- workflows/postprocess_modflow.py (NEW): reads gwt_model.ucn (flopy HeadFile
  CONCENTRATION), final-timestep max-over-layers, compute_plume_metrics (>0.001 floor),
  reproject UTM->EPSG:4326 COG, publish_layer dispatch (non-fatal), returns PlumeLayerURI.
- tools/run_modflow_tool.py (NEW): run_modflow_job atomic tool (FR-DC-6 workflow_dispatch),
  local/cloud paths, reuses solver.wait_for_completion (cancel seam solver-agnostic).
- tools/__init__.py (surgical 1 line): import run_modflow_tool.
- categories.py (surgical 2 lines): run_modflow_job -> hazard_modeling + desc update.
- tests/test_run_modflow.py (NEW): 12 tests, all pass.

## Invariants Touched
Invariant 1 (determinism): preserves — plume numbers from typed PlumeLayerURI fields.
Invariant 2 (deterministic workflows): preserves — no LLM in the chain.
Invariant 3 (engine registration not modification): preserves.
Invariant 8 (cancellation): preserves — ExecutionHandle cancel chain via wait_for_completion.
Invariant 9 (confirmation before consequence): preserves — server hook fires; no cost field.

## Open Questions
- OQ-227-FLOPY-DEP (non-blocking, TENTATIVE: skip): flopy is a transitive dep in
  services/agent/pyproject.toml; resolves at runtime + all tests pass; recommend a
  follow-up one-line "flopy>=3.9,<4" add for a reproducible deployed image. Did not edit
  the shared pyproject (concurrent edits + not my ownership).
- OQ-227-PLUME-PRESET-QML (non-blocking): COG tagged continuous_plume_concentration;
  matching QML authored by the engine styles follow-up (like continuous_flood_depth.qml).
- OQ-227-CLOUD-GEOREG (non-blocking, partial): postprocess reads the COG transform from
  the deck flopy modelgrid. Local mode has deck_dir on-disk (works). The cloud path's
  agent does not hold the deck after submit, so cloud postprocess falls back to identity
  transform unless the deck gwt_model.dis is fetched for the transform (or the entrypoint
  emits a grid-meta JSON). Case 2 demo uses the local path; cloud georeg is a follow-up.
- OQ-227-CONCURRENT-SHARED-FILES (informational): concurrent job-0230 commit (4f78f5c)
  carried my tools/__init__.py + categories.py registration edits into the tree; verified
  present by test_run_modflow.py.

## Dependencies and Impacts
Depends on: job-0220 (entrypoint), job-0221 (gwt_adapter), job-0222 (contracts),
infra/modflow.tf. Affects: job-0228 (Case 2 composer) + job-0235 (acceptance) consume
run_modflow_job; note the cloud-path georeg caveat (OQ-227-CLOUD-GEOREG).

## Verification
- pytest services/agent/tests/test_run_modflow.py -> 12 passed (live local mf6 E2E +
  completion-schema + deck-layout + ExecutionHandle + postprocess math + registration).
- Regression: test_categories.py, test_allowed_set.py, test_postprocess_flood.py pass.
  (test_solver.py + test_model_flood_scenario.py have 2+2 PRE-EXISTING env failures:
  google-cloud-workflows / google-cloud-run not installed in local venv — not my change;
  those files are outside my ownership.)
- Live E2E evidence (REQUIRED): reports/inflight/job-0227-agent-20260609/evidence/
  live_e2e.log, plume_summary.json (max=1122.34, area=0.0275, bbox EPSG:4326),
  mf6_normal_termination.txt ("Normal termination"), completion.json (status:ok,
  converged:true, model_crs:EPSG:32617), manifest.json (20 inputs gwf/+gwt/ + model_crs).
- No Gemini/Vertex calls — programmatic only (direct Python + local mf6 + pytest).
- Results: pass.
