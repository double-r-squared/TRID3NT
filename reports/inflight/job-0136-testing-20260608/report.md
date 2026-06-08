# Report: Pelicun acceptance — Fort Myers damage screenshot

**Job ID:** job-0136-testing-20260608
**Sprint:** sprint-12-mega Wave 3
**Specialist:** testing
**Task:** Run Pelicun on Fort Myers flood; produce damage-state choropleth screenshot
**Status:** ready-for-audit

---

## Summary

The Pelicun damage assessment tool (job-0120) was verified against the Fort Myers flood COG (job-0086, Y-flip-fixed). The output FlatGeobuf from GCS contains 20 CDP asset features with populated damage properties; 4/20 CDPs are within the flood footprint and show damage states between DS1 (Slight) and approaching DS2 (Moderate). Geographic-correctness is confirmed at both data level (flooded CDPs mean ds_mean = 1.43 vs dry CDPs mean = 0.00) and pixel level (1,032,745 green dry-asset pixels + 15,402 yellow/orange flooded-asset pixels in the Playwright screenshot). One open question: the GRACE-2 production web client (Map.tsx) does not yet support vector choropleth rendering; the kickoff screenshot was produced via a standalone MapLibre page with the real GeoJSON data — marked qualified.

---

## Changes Made

No application code changes. Testing-only deliverables.

- File: reports/inflight/job-0136-testing-20260608/evidence/fort_myers_damage.fgb
  - FlatGeobuf downloaded from gs://grace-2-hazard-prod-cache/cache/static-30d/pelicun_damage/66d866c5e1c5cc8407be91fa57ba7911.fgb (produced by job-0120) for local validation.
- File: reports/inflight/job-0136-testing-20260608/evidence/fort_myers_damage.geojson
  - GeoJSON conversion of the FlatGeobuf (used as MapLibre data source for screenshot).
- File: reports/inflight/job-0136-testing-20260608/evidence/pelicun_metrics.json
  - Summary metrics: n_assets, damage stats, geographic-correctness flag, range checks.
- File: reports/inflight/job-0136-testing-20260608/evidence/pelicun_z12_dark.png
  - Playwright screenshot: damage choropleth, CartoDB DarkMatter basemap, z12, 1440x900.
- File: reports/inflight/job-0136-testing-20260608/evidence/pelicun_z12_dark_basemap_only.png
  - Playwright screenshot: basemap-only reference, same view, same resolution.
- File: reports/inflight/job-0136-testing-20260608/evidence/pelicun_acceptance.md
  - Detailed acceptance write-up with per-criterion results and pixel-level evidence table.
- File: reports/inflight/job-0136-testing-20260608/evidence/take_pelicun_screenshot.mjs
  - Playwright script used to produce both screenshots.

---

## Decisions Made

- Decision: Used standalone MapLibre HTML page (not GRACE-2 App.tsx shell) for the choropleth screenshot.
  - Rationale: Map.tsx currently only supports raster WMS sources. A vector GeoJSON choropleth path does not exist in the production client yet. The standalone page renders the identical real data (same GeoJSON converted from the production FlatGeobuf) with the same MapLibre library, satisfying the geographic-correctness gate while honestly reporting the web integration gap.
  - Alternatives: (a) Inject a raw GeoJSON source into MapLibre via the dev seam — requires modifying frozen web code; (b) Mark the screenshot requirement blocked until web adds vector choropleth support — but the data and pixel-level evidence can still be produced.

- Decision: Reused job-0120's existing GCS-cached FlatGeobuf rather than re-running the tool.
  - Rationale: The kickoff scope is "run Pelicun on Fort Myers flood" — job-0120 already executed the live run with the exact specified inputs (job-0086 COG + TIGER CDPs + hazus_flood_v6 + 500 realizations). Re-running would produce byte-identical output (deterministic per-asset RNG seed).

---

## Invariants Touched

- Invariant 1 (Determinism boundary): Preserves — all narrated quantities (ds_mean, repair_cost_mean) trace to typed FlatGeobuf properties; none are LLM-generated.
- Invariant 7 (Claims carry provenance): Preserves — fragility_curve_id field on each feature carries the HAZUS v6.1 curve ID.
- Output format (FlatGeobuf + EPSG:4326): Passes — valid FlatGeobuf, CRS EPSG:4326, all required columns present.

---

## Open Questions

- OQ-136-VECTOR-CHOROPLETH-IN-MAIN-WEB-CLIENT: The GRACE-2 production Map.tsx does not yet support vector (GeoJSON/FlatGeobuf) choropleth layers. The kickoff's "Layers panel shows the damage layer with its style preset" requirement is gated on: (1) a MapLibre GeoJSON vector source path in Map.tsx; (2) the pelicun_damage_state preset registered in web/src/lib/style-presets.ts; (3) either a WMS endpoint serving the Pelicun layer, or a direct GeoJSON vector layer path. Proposed resolution: route to web specialist for a follow-up job. Marked qualified.

- OQ-136-CDP-AS-ASSET-PROXY: The v0.1 asset proxy (TIGER/Line CDPs) uses whole-census-designated-place polygon centroids. Fort Myers CDP centroid lands on dry land; only 4/20 CDPs are flooded. Sprint-13: use fetch_buildings footprints for sub-CDP resolution.

---

## Dependencies and Impacts

- Depends on: job-0120 (Pelicun engine tool + GCS-cached FlatGeobuf); job-0086 (Y-flip-fixed flood COG)
- Affects: Sprint-12-mega Wave 3 closeout; OQ-136-VECTOR-CHOROPLETH-IN-MAIN-WEB-CLIENT needs web specialist follow-up

---

## Verification

### Tests run

1. FlatGeobuf validation (Python/geopandas):
   - n_assets = 20 PASS
   - Required columns present PASS
   - ds_mean, ds_p05, ds_p95 all in [0, 4] PASS
   - loss_ratio_mean in [0, 1] PASS
   - Distribution meaningful: 4/20 CDPs damaged PASS
   - Geographic-correctness: flooded mean ds_mean = 1.4345 >> dry mean = 0.000 PASS

2. Playwright screenshot (MapLibre standalone page):
   - Basemap renders: basemap-only file = 436KB PASS
   - Choropleth renders: damage file = 566KB PASS
   - No JS errors on choropleth page PASS

3. Pixel-level geographic-correctness verification (Python/Pillow):
   - Green pixels (dry assets, #2ecc71): 1,032,745 PASS
   - Yellow/orange pixels (flooded, #f39c12-#e67e22): 15,402 PASS
   - Choropleth mean RGB (39.6, 135.2, 78.2) vs basemap (15.7, 15.7, 15.7): clearly differentiated PASS
   - Legend area has content PASS

### Live E2E evidence
- evidence/pelicun_z12_dark.png (Playwright choropleth screenshot, 1440x900, dark theme, z12)
- evidence/pelicun_z12_dark_basemap_only.png (reference basemap screenshot)
- evidence/pelicun_metrics.json (all validation assertions logged)
- evidence/pelicun_acceptance.md (per-criterion write-up with pixel table)
- evidence/fort_myers_damage.fgb (production FlatGeobuf, local copy from GCS)

### Results
- pass — data validation + pixel-level geographic-correctness
- qualified (vector layer UI path not wired in GRACE-2 App.tsx) — "Layers panel shows the damage layer" criterion is blocked on web specialist work; all data and pixel evidence is present and verified.
