# Report: Case 1 live acceptance — Everglades/Big Cypress flood + habitat E2E

**Job ID:** job-0134-testing-20260608
**Sprint:** sprint-12-mega Wave 3
**Specialist:** testing
**Task:** Run the Case 1 composer end-to-end against a real Florida bbox; produce headline screenshot evidence.
**Status:** ready-for-audit

## Summary

`model_flood_habitat_scenario` executed live against the Big Cypress / Everglades bbox (-81.5, 25.7, -80.7, 26.5) with 3 corrected GBIF species keys (OQ-0117 resolution). All 3 species FlatGeobuf layers + the WDPA protected-areas layer were fetched and written to GCS. The flood model produced an honest failure (BBOX_INVALID — 7123 km² exceeds the 5000 km² v0.1 guardrail for fetch_river_geometry); this is the expected substrate behavior per kickoff §1. Geographic-correctness gate confirmed 100% of all features (244 + 4439 + 5000 points, 23 polygons) fall within the requested bbox. All integration tests pass; 2 pre-existing m4 failures are unchanged.

## Changes Made

- File: `reports/inflight/job-0134-testing-20260608/evidence/case1_capture.py`
  - Direct-invocation live capture script; runs composer, validates geographic correctness, writes case1_metrics.json + case1_acceptance.md

- File: `reports/inflight/job-0134-testing-20260608/evidence/case1_playwright_capture.py`
  - Playwright screenshot script; injects Case 1 session state into running Vite dev server, captures 3 screenshots

- File: `reports/inflight/job-0134-testing-20260608/evidence/case1_metrics.json`
  - CaseOneResult dump including geographic-correctness verification (4 FlatGeobuf layers verified)

- File: `reports/inflight/job-0134-testing-20260608/evidence/case1_acceptance.md`
  - Written by case1_capture.py; geographic-correctness gate write-up (8 pass, 0 warn, 0 fail)

- File: `reports/inflight/job-0134-testing-20260608/evidence/case1_z11_dark.png`
  - Headline screenshot: Big Cypress / Everglades, LayerPanel showing 4 layers

- File: `reports/inflight/job-0134-testing-20260608/evidence/case1_z11_dark_basemap_only.png`
  - Alignment proof: basemap only, same view, overlays hidden

- File: `reports/inflight/job-0134-testing-20260608/evidence/case1_z11_dark_layers_panel.png`
  - LayerPanel showing 4 loaded layers

- File: `tests/m5/test_model_flood_habitat_scenario.py`
  - Pytest live acceptance test (env-gated live_m5); runs directly against real GBIF + WDPA + GCS

## Decisions Made

- Decision: Use corrected species keys from _species_reference.py (OQ-0117), not the kickoff audit.md originals
  - Rationale: OQ-0117 verified that audit keys 2481008 (Roseate spoonbill) and 2436873 (American alligator) were wrong taxa. Corrected to 2480803 (Platalea ajaja) and 2441370 (Alligator mississippiensis).
  - Alternatives: use the kickoff keys (would produce 0 or wrong records in bbox)

- Decision: Accept flood modeling honest failure (BBOX_INVALID) as the pass state
  - Rationale: The Big Cypress bbox is 7123 km² -- exceeds the v0.1 5000 km² guardrail. Kickoff §1 explicitly accepts this as the acceptance state.
  - Alternatives: use a smaller bbox (would not match the kickoff spec)

## Invariants Touched

- Invariant 1 (Determinism boundary): preserves -- case_summary_text is a deterministic format-string; all field values come from typed tool returns.
- Invariant 2 (Deterministic workflows): preserves -- zero LLM calls in the composer chain confirmed.
- Invariant 7 (Claims carry provenance): preserves -- LayerURIs carry GCS URIs; composer threads provenance through unchanged.

## Open Questions

- OQ-0134-SPECIES-COUNTS-GS-URI: _count_features_safely in the composer uses pyogrio on gs:// URIs which fail silently. species_counts shows 0 for keys 2441370 and 2480803 even though the FlatGeobuf files contain 4439 and 5000 records. The actual feature counts were verified via direct GCS download. Proposed fix: download to tmp file if URI starts with gs://. Route to engine. TENTATIVE: surface as known-quality gap; does not block acceptance.

- OQ-0134-DARK-THEME-MAP-COMMAND: The load-style dark map command fired in the Playwright script but screenshots show the light basemap. Cosmetic for acceptance evidence; geography and layer presence verified correctly.

- OQ-0134-IMPACT-METRICS-EMPTY: impact_metrics is empty because flood layer failed (BBOX_INVALID), so compute_zonal_statistics never ran. Expected behavior documented in the composer.

## Dependencies and Impacts

- Depends on: job-0118 (model_flood_habitat_scenario), job-0117 (_species_reference.py), job-0087 (fetch_gbif_occurrences), job-0089 (fetch_wdpa_protected_areas), job-0042 (model_flood_scenario)
- Affects: sprint-12-mega exit criterion "Case 1 live demo screenshot" -- PARTIAL PASS (3 species layers + WDPA confirmed; flood honest-failure per kickoff §1)

## Verification

- Tests run:
  - tests/integration/ -- 7 passed, 0 failed
  - tests/m4/ -- 2 pre-existing failures confirmed pre-existing by git-stash verification (unchanged)
  - tests/m5/test_model_flood_habitat_scenario.py::test_model_flood_habitat_scenario_live -- 1 passed (26.15s, using GCS cache hit)
- Live E2E evidence:
  - evidence/case1_metrics.json -- CaseOneResult with geographic-correctness verification
  - evidence/case1_acceptance.md -- geographic gate: 8 pass, 0 warn, 0 fail
  - evidence/case1_z11_dark.png -- Playwright screenshot, LayerPanel 4 rows
  - evidence/case1_z11_dark_basemap_only.png -- alignment proof
  - evidence/case1_z11_dark_layers_panel.png -- LayerPanel (4 layers: WDPA + 3 GBIF)
  - All 4 FlatGeobuf GCS objects verified: 244 + 4439 + 5000 species points + 23 WDPA polygons -- 100% within bbox
- Results: QUALIFIED PASS
  - Geographic-correctness gate: PASS (pixel-level -- all features inside requested bbox)
  - Species layer returns: PASS (3/3 GCS URIs confirmed)
  - WDPA layer return: PASS (23 polygons, includes Everglades NP + Big Cypress NP)
  - Flood layer: HONEST FAILURE (BBOX_INVALID, expected, per kickoff §1)
  - Impact metrics: empty (blocked by flood failure, expected)
  - case_summary_text: PASS (deterministic, non-empty)
  - Dark theme: QUALIFIED (OQ-0134-DARK-THEME-MAP-COMMAND)
  - Regression: 0 new failures
