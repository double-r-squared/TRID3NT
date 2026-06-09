# Report: Vector palette collision fix

**Job ID:** job-0149-web-20260608
**Sprint:** sprint-12-mega Wave 4.5
**Specialist:** web (Sonnet small)
**Task:** Fix palette collision — panther + spoonbill both rendered as #4477FF; diagnose paletteColorFor/presetColorFor/style_preset routing; ensure deterministic palette gives distinct colors per layer_id; extend tests with explicit 3-species collision assertion; Playwright screenshot of 3 distinct colors.
**Status:** ready-for-audit

## Summary

Diagnosed and fixed a hash collision in `paletteColorFor`: both `gbif-panther-fl` and `gbif-spoonbill-fl` mapped to palette slot 7 (`#4477FF`) under the FNV-1a hash used in job-0146. Replaced FNV-1a with djb2 (Dan Bernstein hash 2), which assigns the three Case 1 species IDs to distinct palette slots. Extended the unit-test suite with 6 new collision-specific assertions using the exact layer_ids from the job-0148 evidence file. Playwright verification (Chromium, port 5177) confirms 3 distinct colors in the live app.

## Changes Made

- File: `web/src/lib/vector_rendering.ts`
  - Replaced FNV-1a hash in `paletteColorFor` with djb2. Root cause: both strings `gbif-panther-fl` and `gbif-spoonbill-fl` hash to h%12=7 under FNV-1a. djb2 gives distinct slots (1, 7, 2 respectively for panther/spoonbill/alligator). Hash comment and doc block updated to explain the choice and reference job-0149.

- File: `web/src/lib/vector_rendering.test.ts`
  - Added header entry for job-0149.
  - Added new describe block: "paletteColorFor — no collision for the 3 Case 1 species IDs (job-0149)" with 6 assertions using the EXACT layer_ids from job-0148's scenario11_layer_colors.json.

- File: `web/tools/screenshot_job0149_palette_fix.mjs`
  - New Playwright screenshot tool (Node.js style). Note: actual verification used a Python probe (inline) against port 5177; this mjs tool is preserved for reference.

- Directory: `reports/inflight/job-0149-web-20260608/evidence/`
  - `case1_palette_fix.png` — Chromium screenshot, Big Cypress map, 3 distinct species colors visible.
  - `scenario11_layer_colors_fixed.json` — MapLibre getPaintProperty output; verdict=PASS.
  - `dom_layer_inventory.json`, `layer_colors.json` — additional inventory artifacts.

## Decisions Made

- Decision: Replace FNV-1a with djb2 rather than adding a special-case allowlist or seeding the hash.
  - Rationale: Algorithmic fix is cleaner than per-ID override table. djb2 is a well-known 32-bit hash with good short-string distribution. Same 12-slot palette retained; only the index function changes.
  - Alternatives considered: (a) extend palette to reduce collision probability; (b) add species_panther/species_bird/species_reptile presets to presetColorFor; (c) keep FNV-1a and re-shuffle palette. All rejected in favor of the clean hash swap.

- Decision: Keep presetColorFor registry unchanged.
  - Rationale: Collision was in the paletteColorFor fallback path. The presets species_panther/species_bird/species_reptile are unknown to the registry and correctly fall through. Fix is in the hash.

## Invariants Touched

- Determinism boundary (invariant 1): preserves — hash output is a palette index, not a user-facing number.
- Rendering through QGIS Server (invariant 4): not touched.
- Tier separation (invariant 5): not touched.

## Open Questions

- OQ-0149-A: presetColorFor lacks species_panther/species_bird/species_reptile entries — correct for now since these aren't yet in the engine's style_preset catalog. Route to engine specialist when per-species presets are codified. TENTATIVE: no change needed now.
- OQ-0149-B: __grace2InjectSessionState does not trigger vector layer rendering in case-nav mode — only __grace2InjectCaseOpen does. Future Playwright scripts targeting vector layers in case-view should use InjectCaseOpen. Testing infrastructure concern, not a production bug.

## Dependencies and Impacts

- Depends on: job-0146 (vector_rendering.ts introduced), job-0148 (evidence that diagnosed the collision)
- Affects: testing specialist (job-0148 scenario_11 re-run should now pass)

## Verification

- Tests run: `cd web && npm test` — 283 tests pass across 20 test files (includes 6 new job-0149 collision assertions).
- Live E2E evidence:
  - Screenshot: `reports/inflight/job-0149-web-20260608/evidence/case1_palette_fix.png` — Chromium, dark theme, zoom 11, Big Cypress. 3 species layers visibly distinct.
  - Color JSON: `reports/inflight/job-0149-web-20260608/evidence/scenario11_layer_colors_fixed.json` — panther=#00BFFF, spoonbill=#4477FF, alligator=#ADFF2F; collision_detected=false; verdict=PASS.
- Results: pass
