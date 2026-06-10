# Audit: Case 2 workflow composer — news -> MODFLOW -> plume

**Job ID:** job-0228-agent-20260609
**Sprint:** sprint-13 (Stage 2)
**Auditor:** Development Orchestrator
**Status:** assigned

# Kickoff (frozen)

You are the agent specialist. Job job-0228-agent-20260609 — Case 2 workflow composer: news -> MODFLOW -> plume (sprint-13 Stage 2, adversarial-verify gated 4-lens).

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

## Inputs in force
- job-0227 just landed run_modflow.py + postprocess_modflow.py + run_modflow_job tool (read them first)
- Existing news-ingest path from sprint-12: model_news_event_ingest.py workflow + aggregate_claims_across_sources tool
- MODFLOWRunArgs contract with demo aquifer defaults (K=1e-4, porosity=0.3, OQ-3 TENTATIVE)

## Scope
services/agent/src/grace2_agent/workflows/model_groundwater_contamination_scenario.py (NEW): composer chaining:
1. news ingest (URL or pasted text) -> claims extraction via the existing aggregate_claims_across_sources machinery -> derive {spill_location_latlon (geocode if needed), contaminant, release_rate_kg_s, duration_days} with explicit unit conversions + plausibility clamps (release rate 1e-6..100 kg/s, duration 0.1..3650 d)
2. CONFIRMATION-BEFORE-CONSEQUENCE: emit a parameter-confirmation envelope (same pattern as the payload-warning user-pause) presenting derived params + demo-aquifer caveat; the MODFLOW submission happens ONLY after user confirm (the composer exposes a confirmed=True bypass arg for programmatic/test use, documented)
3. run_modflow_job -> postprocess -> publish -> PlumeLayerURI + narrative summary dict {plume_area_km2, max_concentration_mgl, location_name}
Tool registration: model_groundwater_contamination_scenario in tools/__init__.py + catalog.py + categories.py (surgical, shared-file warning applies).
Synthetic fixture: write tests/fixtures/case2_news_article.txt — realistic ~400-word article about a solvent (TCE) tanker spill near a named small city (NOT Florida) with quantities derivable (e.g. "12,000 gallons over roughly six hours") — manifest OQ-2 allows synthetic fixture, flag it as synthetic in report.md.

## Acceptance
- [REQUIRED live] Programmatic end-to-end on the synthetic fixture with GRACE2_MODFLOW_LOCAL=1: article text -> extracted+clamped params (assert geographically/physically plausible: lat/lon within CONUS state of the article, release rate in clamp range) -> confirmation envelope emitted -> (confirmed=True) -> local mf6 run -> plume layer summary non-zero. Evidence log + extracted-params JSON to reports/inflight/<job-id>/evidence/.
- pytest tests/test_model_groundwater_contamination_scenario.py: extraction unit conversions (gallons->kg via density, hours->days), clamps, confirmation gate blocks without confirm, full chain with mf6 mocked.

## File ownership
workflows/model_groundwater_contamination_scenario.py, tests/test_model_groundwater_contamination_scenario.py, tests/fixtures/case2_news_article.txt + surgical registration lines.
