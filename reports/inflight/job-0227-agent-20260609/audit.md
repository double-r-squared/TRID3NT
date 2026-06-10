# Audit: MODFLOW Cloud Workflows integration + run_modflow tool binding (sprint-13 Stage 2)

**Job ID:** job-0227-agent-20260609
**Sprint:** sprint-13
**Auditor:** Development Orchestrator
**Status:** assigned

# Kickoff (frozen)

You are the agent specialist. Job job-0227-agent-20260609 — MODFLOW Cloud Workflows integration + run_modflow tool binding (sprint-13 Stage 2).

## Common rules (GRACE-2 sprint-13 Stage 2)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, your specialist file in agents/, reports/sprints/sprint-13-manifest.md (your job scope), reports/PROJECT_STATE.md.
FIRST ACTION: mkdir -p reports/inflight/<job-id>/ ; write audit.md with this kickoff verbatim under "# Kickoff (frozen)"; STATE file "RUNNING".
- NO Gemini/Vertex generate_content calls. Hard rule. All live evidence is produced programmatically (direct Python invocation, local mf6 binary, vitest/pytest) — never through the chat loop.
- NEVER git push. Commit locally at job end: git add <only your files> && git commit -m "<job-id>: <title>". index.lock conflicts: wait 5s, retry 5x.
- SHARED REGISTRATION FILES WARNING: other Stage 2 agents are concurrently editing services/agent/src/grace2_agent/tools/__init__.py, catalog.py, categories.py, and adapter.py. Re-read each shared file IMMEDIATELY before editing it; keep edits surgical (single anchor); if an Edit fails on a stale anchor, re-read and retry.
- Environment: no docker daemon, no gcloud on this box; mf6 6.5.0 static binary is downloadable and runs locally (see reports/inflight/job-0220-infra-20260609/evidence/mf6_smoke.log); tofu validate only.
- Python venv: services/agent/.venv. Web: npx vitest in web/.
- Report honestly; PARTIAL with documented blockers beats fake success.
- AT JOB END: write reports/inflight/<job-id>/report.md, STATE "READY_FOR_AUDIT".
Return StructuredOutput.

## Inputs now in force (read them)
- services/workers/modflow/ — container + entrypoint (job-0220, commit 75f57ff + fix d839d64) and gwt_adapter.py (job-0221, commit b042f1b)
- packages/contracts/src/grace2_contracts/modflow_contracts.py — MODFLOWRunArgs + PlumeLayerURI (job-0222)
- infra/modflow.tf — Cloud Run Job + Workflows skeleton
- The SFINCS analog: run_solver/wait_for_completion pattern from job-0041, model_flood_scenario workflow

## Scope
1. services/agent/src/grace2_agent/workflows/run_modflow.py (NEW): deck-build + submit + postprocess orchestration:
   - build deck via services/workers/modflow/gwt_adapter.build_modflow_deck from a MODFLOWRunArgs
   - CRITICAL handoff fixes from Stage 1 OQs: populate the manifest model_crs field and upload the deck in the gwf/ + gwt/ subdir layout the entrypoint reconstructs (design-doc section 6 dest paths; the entrypoint was verified against this layout — read entrypoint.py to match it exactly)
   - submit via Cloud Workflows execution (same client pattern as the SFINCS run_solver path), return ExecutionHandle, emit progress envelopes
   - LOCAL EXECUTION MODE (GRACE2_MODFLOW_LOCAL=1): run the deck against a locally-downloaded mf6 binary instead of Cloud Workflows — this is your live-evidence path on this machine AND the dev/test seam (mirror how sandbox/local fallbacks are done elsewhere)
2. services/agent/src/grace2_agent/workflows/postprocess_modflow.py (NEW): read MF6 GWT concentration output (UCN/HDS-style binary via flopy), take final-timestep max-over-layers concentration grid, reproject to EPSG:4326 COG, compute max_concentration_mgl + plume_area_km2 (cells above a 0.001 mg/L floor), return PlumeLayerURI. publish_layer dispatch included (callable, mocked in tests).
3. services/agent/src/grace2_agent/tools/run_modflow_tool.py (NEW): atomic tool run_modflow_job(...MODFLOWRunArgs fields...) wrapping the workflow; registration in tools/__init__.py + catalog.py + categories.py (hazard-modeling category alongside run_model_flood_scenario).

## Acceptance
- [REQUIRED live] GRACE2_MODFLOW_LOCAL=1 end-to-end: MODFLOWRunArgs -> deck -> local mf6 run to Normal termination -> postprocess -> PlumeLayerURI with non-zero max_concentration_mgl and plume_area_km2 > 0. Save log + plume summary to reports/inflight/<job-id>/evidence/.
- pytest services/agent/tests/test_run_modflow.py: deck layout matches entrypoint expectations (gwf/+gwt/ subdirs + model_crs), ExecutionHandle shape, postprocess math on synthetic concentration arrays, registration presence.

## File ownership
workflows/run_modflow.py, workflows/postprocess_modflow.py, tools/run_modflow_tool.py, tests/test_run_modflow.py + surgical registration lines.
