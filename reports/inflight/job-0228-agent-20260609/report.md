# Report: Case 2 workflow composer — news -> MODFLOW -> plume

**Job ID:** job-0228-agent-20260609
**Sprint:** sprint-13 (Stage 2)
**Specialist:** agent
**Status:** ready-for-audit

## Summary
Built the Case 2 composer `model_groundwater_contamination_scenario` (MODFLOW analogue of Case 1 `model_flood_habitat_scenario`). Turns a spill news article (pasted text or URL) into a rendered groundwater-contaminant plume: extracts location + scale + contaminant + duration, derives MODFLOW forcing with explicit unit conversions (gallons->kg via density, hours->days, rate = mass/duration) + plausibility clamps (rate 1e-6..100 kg/s, duration 0.1..3650 d), gates the solver run behind a parameter-confirmation envelope (payload-warning pattern, fail-closed), runs `run_modflow_job`, returns `Case2Result` with `PlumeLayerURI` + narration summary. Live E2E against the synthetic Twin Falls, Idaho TCE fixture with real mf6 6.5.0 -> non-zero plume (max 2946.32 mg/L, area 0.0125 km2).

## Changes Made
- NEW workflows/model_groundwater_contamination_scenario.py — composer + extractors + local Case2Result + run_model_groundwater_contamination_scenario wrapper (workflow_dispatch, FR-DC-6).
- NEW tests/test_model_groundwater_contamination_scenario.py — 17 tests.
- NEW tests/fixtures/case2_news_article.txt — SYNTHETIC ~430-word TCE tanker-spill article near Twin Falls, Idaho (NOT Florida), flagged synthetic (manifest OQ-2).
- EDIT workflows/__init__.py — one import line (registration at startup).
- EDIT categories.py — one PRIMARY_CATEGORY line (hazard_modeling) + one SECONDARY_CATEGORIES line (news_events).
- NO edit to tools/catalog.py: that file is the public DATA-SOURCE catalog (YAML), not the LLM tool surface; a composer has no entry there. Registration is workflows/__init__.py + categories.py.

## Decisions
- Confirmation gate reuses the payload-warning envelope (kickoff: "same pattern as the payload-warning user-pause") via an injected `confirmation_hook`; no hook AND confirmed=False -> fail-closed. `confirmed=True` is the documented programmatic/test bypass; the server per-solver confirmation hook around run_modflow_job is the independent fail-closed backstop. No cost fields (estimated_mb=threshold_mb=0).
- Case2Result kept LOCAL to the agent (not packages/contracts): agent-only contract scope + concurrent schema edits. OQ-0228-CASE2RESULT-PROMOTION.
- Composer-level contaminant (solvent bag: TCE/PCE/DCM/toluene/...) + duration extractors fill the deterministic aggregator's gaps; aggregator reused for location + scale; best-location selection defends against the aggregator regex over-matching across newlines.

## Invariants
- 1 Determinism: preserves (all narrated numbers typed; no LLM). 2 Deterministic workflows: preserves. 3 Engine registration: preserves (reuses run_modflow_job). 8 Cancellation: preserves (CancelledError bubbles; geocode catch catches Exception not BaseException). 9 Confirmation before consequence: preserves (gated, fail-closed). 10 Minimal parameter surface: preserves.

## Open Questions
- OQ-0228-CONFIRM-ENVELOPE-CHOICE (TENTATIVE): payload-warning vs A.4 confirmation-request for the composer gate; recommend payload-warning (web renders inline). One-line swap if orchestrator prefers A.4.
- OQ-0228-CASE2RESULT-PROMOTION: promote Case2Result to schema only if it must be on the wire.
- OQ-0228-CONTAMINANT-DENSITY-COVERAGE: curated density table + solvent bag (TENTATIVE); unknown -> water-like with a note; subsumed by OQ-93-NEEDS-LLM-EXTRACTION.
- OQ-0228-GEOCODE-OFFLINE-EVIDENCE: live E2E injects an offline Twin Falls geocode (Nominatim is network); everything downstream (flopy deck, mf6 solve, rasterio reproject) is real.

## Verification
- pytest tests/test_model_groundwater_contamination_scenario.py -> 17 passed. tests/test_categories.py (incl. every-tool-has-a-primary-category) + test_run_modflow.py + test_allowed_set.py -> pass. Broad subset -> 394 passed, 2 failed, 15 skipped. The 2 failures are test_model_flood_scenario.py::{returns_layer_uri, triggers_loaded_layers_emit} — PRE-EXISTING ENVIRONMENT-ONLY (google.cloud.run_v2 not importable -> publish_layer falls back to gs:// -> tests assert https://). They do not reference my files; not a regression.
- LIVE E2E: evidence/run_case2_e2e.py ran the composer on the synthetic fixture with GRACE2_MODFLOW_LOCAL=1 + real /tmp/mf6 6.5.0. Transcript evidence/case2_e2e.log; extracted_params.json; plume_summary.json.
  - Extraction: contaminant=trichloroethylene, location="Twin Falls, Idaho", point (42.563,-114.461) IN Idaho band; rate=3.0704 kg/s IN clamp; duration=0.25 d IN clamp.
  - Confirmation envelope emitted (tool=run_modflow_job, options=[proceed,cancel], demo-aquifer caveat); approve -> MODFLOW.
  - Real mf6 solve -> plume max_concentration_mgl=2946.32, plume_area_km2=0.0125 (non-zero). All assertions PASSED.
- Results: pass (live E2E + unit); the 2 unrelated flood-scenario failures are documented env limitations.
