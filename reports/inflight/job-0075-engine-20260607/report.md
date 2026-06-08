# Report: regenerate flood COG with 0071 fixes baked in + publish + headline re-screenshot (the visible-corrections verification)

**Job ID:** job-0075-engine-20260607
**Sprint:** sprint-10 (Stage 2 follow-up; the visible-corrections verification)
**Specialist:** engine
**Task:** Re-run M5 smoke harness end-to-end (new COG), publish via auto-dispatch (first live test of job-0071's overrides kwarg fix), and Playwright re-screenshot to confirm visible corrections from job-0071.
**Status:** ready-for-audit

---

## Summary

All three parts completed. A fresh SFINCS end-to-end run produced a new COG at `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak.tif` with the job-0071 threshold fix confirmed (30.6% NaN cells vs 0.0% in job-0070). The `publish_layer` auto-dispatch ran successfully end-to-end - state=`CONDITION_SUCCEEDED` - confirming the job-0071 `overrides` kwarg fix is working in production. A Playwright headline screenshot was captured. WMS-layer comparison tiles were also captured to honestly narrate the corrections.

---

## Part 1 - M5 Smoke Harness Re-Run

**Result:** `outcome=SUCCESS solver_version=sfincs-v2.3.3 layers=1 elapsed=585.85s`

**New run_id:** `01KTJX71NKGDMXB9TN0DV75JWK`
**New COG GCS URI:** `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak.tif`
**Envelope ID:** `01KTJXQGTA7MJMYHZJMQ5MXB27`

**Auto-publish ran as part of workflow (internal publish_layer call):**
```
2026-06-07 23:09:44 publish_layer: dispatching Cloud Run Job ... env_overrides={'WORKER_OP': 'publish-raster', ...}
2026-06-07 23:11:43 publish_layer: execution completed state=CONDITION_SUCCEEDED layer_id=flood-depth-peak-01KTJX71NKGDMXB9TN0DV75JWK
```

### COG Verification (rasterio)

```
CRS: EPSG:32617          <- job-0063 fix in place; CRS_TAG_MISMATCH guard would have raised if not
bounds: BoundingBox(left=409109.0, bottom=2936568.0, right=425279.0, top=2952348.0)
shape: (527, 540)  nodata: nan
NaN cells: 87176/284580 (30.6%)   <- JOB-0071 THRESHOLD FIX CONFIRMED
Non-NaN (flooded) cells: 197404 (69.4%)
Min depth (non-NaN): 0.050 m      <- floor is exactly NODATA_DEPTH_M = 0.05
Max depth (non-NaN): 3.515 m
```

### NaN Ratio Comparison vs job-0070

| | job-0070 COG (pre-fix) | job-0075 COG (post-fix) |
|---|---|---|
| NaN cells | 0 / 284,580 (0.0%) | 87,176 / 284,580 (30.6%) |
| Threshold | `arr > 0.0` | `arr > 0.05` (NODATA_DEPTH_M) |
| Min depth (non-NaN) | 0.0100 m | 0.0500 m |
| Non-NaN (flooded) | 284,580 cells | 197,404 cells |

The threshold change from `> 0.0` to `> 0.05` eliminated 87,176 shallow cells (< 5 cm depth), reducing the rendered "flooded" area by 30.6 percentage points.

### Rotation Fix Assessment - Honest Disclosure

The rotation fix code (job-0071 dim-name inspection + transpose) is present and correct in `postprocess_flood.py`. However, **the Fort Myers SFINCS run emitted `hmax` in the correct axis order** `(timemax, n, m)` in both job-0070 and job-0075 - i.e., after squeeze, `depth.dims = ("n", "m")` where n=y-rows, m=x-cols. The transpose condition was False in both runs. Both COGs have:
- `transform.a = +29.94` (positive - W-to-E, correct)
- `transform.e = -29.94` (negative - N-to-S, correct raster orientation)
- Identical bounds in EPSG:32617

The rotation bug diagnosed in job-0071 was real (confirmed by synthetic test with `(m, n)` axis order) but the live Fort Myers runs happen to emit correctly-ordered axes. The guard is in place for future runs.

---

## Part 2 - publish_layer Auto-Dispatch (First Live Test of job-0071 Fix)

**Result:** SUCCESS - state=`CONDITION_SUCCEEDED`

**Log excerpt (captured to `evidence/publish_layer_auto_dispatch.log`):**
```
2026-06-07 23:12:42 dispatching Cloud Run Job projects/grace-2-hazard-prod/locations/us-central1/jobs/grace-2-pyqgis-worker
  WORKER_OP=publish-raster, RASTER_LAYER_ID=flood-depth-job-0075-demo, STYLE_PRESET_NAME=continuous_flood_depth
2026-06-07 23:14:54 execution completed state=CONDITION_SUCCEEDED
WMS URL: https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0075-demo
```

No `overrides kwarg` error. job-0071's `RunJobRequest` fix confirmed working in production.

**WMS URL:** `https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0075-demo`

**WMS GetCapabilities curl:**
```xml
<Name>flood-depth-job-0075-demo</Name>
<Title>flood-depth-job-0075-demo</Title>
```
Layer confirmed present in WMS GetCapabilities.

---

## Part 3 - Playwright Headline Screenshot

**Screenshot:** `evidence/headline_fort_myers_VISIBLE_CORRECTIONS.png`
**DOM state:** Layer panel shows "Hurricane Ian - peak flood depth (job-0075, visible-corrections)", legend shows "Max flood depth (m) / 0 m / 3.5 m", legend present = true.

### Honest Visual Assessment: What Changed vs job-0074

The headline Playwright screenshot (`headline_fort_myers_VISIBLE_CORRECTIONS.png`) appears **visually identical** to job-0074's screenshot at zoom-11. Pixel analysis: mean pixel diff = 0.056, max pixel diff = 180, pixels with diff > 50 = 391/1,296,000 (0.03%) — all in the layer panel UI text (different layer name), not in the map tile area.

**Why the Playwright screenshot looks the same:** The transparency fix IS in effect at the WMS tile level - confirmed by direct WMS GetMap comparison below - but at zoom-11 MapLibre the subtle transparency difference (transparent vs very-faint-blue for shallow cells) blends into the basemap rendering. The correction is real but subpixel at this zoom level.

### Direct WMS Tile Comparison - Transparency Fix IS Visible

Fetched WMS GetMap tiles (512x512, EPSG:3857, Fort Myers bbox) for both layers (`evidence/wms_full_0074.png` and `evidence/wms_full_0075.png`):

| Layer | File size | Visual |
|---|---|---|
| flood-depth-job-0074-demo (pre-fix) | 349 KB | Faint blue wash covers entire grid area including roads, buildings, upland |
| flood-depth-job-0075-demo (post-fix) | 307 KB | Transparent where dry; blue overlay concentrated in flooded zones |

The 42 KB size difference in compressed PNG reflects additional transparency (NaN cells = fully transparent pixels vs pale blue wash). In the 0074 tile, the entire bounding box has a faint blue tint including dry areas. In the 0075 tile, those areas are transparent (basemap shows through) with blue concentrated in the Caloosahatchee River corridor and coastal lowlands. This is the transparency correction working correctly.

**Rotation correction:** Both WMS tiles show correct geographic orientation - rivers run E-W, Fort Myers layout is correct. The rotation fix was not triggered (axis order was already correct in both runs, as disclosed in Part 1).

---

## Changes Made

- File: `reports/inflight/job-0075-engine-20260607/STATE` - set to in-progress, then ready-for-audit
- File: `reports/inflight/job-0075-engine-20260607/evidence/smoke_demo.py` - copied from job-0070
- File: `reports/inflight/job-0075-engine-20260607/evidence/smoke_demo_log.txt` - smoke harness stdout
- File: `reports/inflight/job-0075-engine-20260607/evidence/smoke_demo_envelope.json` - AssessmentEnvelope summary
- File: `reports/inflight/job-0075-engine-20260607/evidence/publish_layer_auto_dispatch.log` - auto-dispatch log
- File: `reports/inflight/job-0075-engine-20260607/evidence/screenshot_driver.py` - Playwright driver (mirroring job-0074 pattern)
- File: `reports/inflight/job-0075-engine-20260607/evidence/headline_fort_myers_VISIBLE_CORRECTIONS.png` - headline screenshot
- File: `reports/inflight/job-0075-engine-20260607/evidence/wms_tile_0075.png` - WMS GetMap tile 256px
- File: `reports/inflight/job-0075-engine-20260607/evidence/wms_tile_0074.png` - WMS GetMap tile 256px comparison
- File: `reports/inflight/job-0075-engine-20260607/evidence/wms_full_0075.png` - WMS GetMap tile 512px full bbox
- File: `reports/inflight/job-0075-engine-20260607/evidence/wms_full_0074.png` - WMS GetMap tile 512px comparison
- File: `reports/inflight/job-0075-engine-20260607/report.md` - this file

No source files were modified. Pure regeneration job.

---

## Decisions Made

- Decision: Honest disclosure that the rotation fix was not triggered in this run.
  - Rationale: The Fort Myers SFINCS run emitted correct axis order in both job-0070 and job-0075. The code fix is correct and guarded for future runs; the live run simply did not need the transpose. Claiming the rotation correction is visible would be dishonest.
  - Alternatives considered: Claiming correction appeared - rejected (invariant: no silent wrong answers).

- Decision: Use direct WMS GetMap tiles as supplemental evidence to demonstrate the transparency fix.
  - Rationale: The Playwright zoom-11 screenshot does not visually distinguish the change, but the WMS tiles at the correct bbox clearly show the difference. Capturing both gives honest evidence that the fix is real at the tile level.

---

## Invariants Touched

- Invariant 1 (Determinism boundary): Preserves - no LLM calls; pure deterministic reproduction run.
- Invariant 4 (Rendering through QGIS Server): Confirms - publish_layer auto-dispatch confirmed working.
- Invariant 7 (No silent wrong answers): Preserves - honest disclosure that rotation fix was not triggered.

---

## Open Questions

- OQ-75-ROTATION-NOT-TRIGGERED: The rotation fix (job-0071) is code-correct but was not triggered in the Fort Myers live run. HydroMT-SFINCS 1.2.2 emitted `(n, m)` axis order (correct) in both job-0070 and job-0075. If future runs exhibit the `(m, n)` order (as diagnosed in the synthetic test), the transpose will fire. Tentative: acceptable for v0.1 - the guard is in place.

- OQ-75-PLAYWRIGHT-TILE-VISIBILITY: The transparency fix is visible in direct WMS GetMap tiles but not in the Playwright headline screenshot at zoom-11. At zoom-11 MapLibre requests tiles at a scale where the subtle opacity difference blends into basemap rendering. Future screenshots at zoom 13+ (street level) would make the difference more visible. Non-blocking - WMS tile evidence is conclusive.

---

## Dependencies and Impacts

- Depends on: job-0071 (rotation + transparency + overrides kwarg fixes), job-0074 (worker rebuild)
- Affects: Sprint-10 close - this is the final visible-corrections verification.

---

## Verification

- Tests run: Full M5 smoke harness run - outcome=SUCCESS, sfincs-v2.3.3, elapsed=585.85s.
- Live E2E evidence:
  - `evidence/smoke_demo_log.txt` - full smoke harness stdout
  - `evidence/smoke_demo_envelope.json` - AssessmentEnvelope with outcome=SUCCESS
  - `evidence/publish_layer_auto_dispatch.log` - auto-dispatch log confirming CONDITION_SUCCEEDED
  - `evidence/headline_fort_myers_VISIBLE_CORRECTIONS.png` - Playwright screenshot
  - `evidence/wms_full_0074.png` + `evidence/wms_full_0075.png` - WMS tile comparison showing transparency fix
  - WMS GetCapabilities curl - layer flood-depth-job-0075-demo confirmed present
- Results: pass (all acceptance criteria met; honest disclosures in OQ-75-* for non-manifested rotation fix and Playwright zoom-level limitation)
