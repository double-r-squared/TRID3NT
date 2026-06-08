# Report: postprocess_flood Y-axis flip fix + regenerate + republish

**Job ID:** job-0086-engine-20260608
**Sprint:** sprint-11 Stage 1 capstone
**Specialist:** engine
**Task:** Y-orientation guard in _extract_peak_depth_geotiff, regression tests, Fort Myers COG regeneration, WMS layer republish, live Playwright verification
**Status:** ready-for-audit

## Summary

The orchestrator's diagnosis was confirmed: SFINCS sfincs_map.nc stores y ascending along rows (y[0,0] = 2,936,568 south UTM, y[-1,0] = 2,952,348 north UTM), while rasterio.transform.from_bounds(...) produces a north-up transform. Writing the array as-is caused a Y-flip: deep flood pixels at the south (Caloosahatchee mouth + San Carlos Bay) painted onto the north of the overlay bbox. The fix inserts Y-orientation and X-orientation guards immediately before the COG write, flipping both arr and arr_masked when SFINCS data is south-at-row-0. The Fort Myers COG was re-extracted with the fix applied, uploaded as flood_depth_peak_0086.tif, and published as WMS layer flood-depth-job-0086-fix. Live Playwright screenshots confirm deep-flood pixels now appear at the SW as expected.

## Changes Made

- File: services/agent/src/grace2_agent/workflows/postprocess_flood.py
  - Added Y-orientation guard (job-0086): detects ascending y along rows; if true, flips arr and arr_masked with [::-1, :] before COG write. Logs y[0,0] to y[-1,0] values at INFO level.
  - Added X-orientation guard (belt-and-suspenders): detects descending x along cols; if true, flips arr and arr_masked with [:, ::-1]. No-op for all standard SFINCS runs.
  - Both guards are defensive (try/except yields warning + identity on probe failure).

- File: services/agent/tests/test_postprocess_flood.py (new)
  - 4 tests: test_y_ascending_gets_flipped, test_y_descending_is_idempotent, test_metrics_are_flip_invariant, test_x_descending_gets_flipped
  - Synthetic netCDF uses realistic UTM Zone 17N coordinates (x ~420000, y ~2937000) to pass the CRS sanity guard.
  - All 4 pass; full suite: 279 passed, 8 skipped, 0 regressions.

## Probe Results

Y-direction: y[0,0] = 2936568.00 (south UTM), y[-1,0] = 2952348.00 (north UTM), ascending_along_rows = True, guard FIRES.
X-direction: x[0,0] = 409109.00, x[-1,0] = 425279.00 (ascending), X guard is no-op (correct).

## Decisions Made

- Place orientation guards between the transform block and the COG write. Metrics are computed before the guards from the masked array and are flip-invariant aggregates (confirmed by test_metrics_are_flip_invariant).
- Use UTM coordinates in synthetic netCDF fixtures: small integer x/y would trigger CRS_TAG_MISMATCH sanity check.
- Upload as flood_depth_peak_0086.tif (preserving the old 0075 COG for comparison per kickoff).

## Invariants Touched

- Determinism boundary (1): preserves
- Deterministic workflows (2): preserves
- Rendering through QGIS Server (4): preserves
- Metadata-payload pattern (6): preserves

## Open Questions

None. Y confirmed ascending, guard fires, screenshots confirm correct orientation. Honest-disclosure path (guard no-op + mirror persists) does not apply.

## Dependencies and Impacts

- Depends on: job-0063, job-0071, job-0075, job-0078
- Affects: flood overlay alignment now correct for Fort Myers run; guard applies to all future SFINCS extractions.

## Verification

Tests: pytest services/agent/tests/ -q -> 279 passed, 8 skipped, 0 regressions (baseline 275+8).

New COG: gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak_0086.tif, shape 527x540, bounds (409109.0, 2936568.0, 425279.0, 2952348.0) EPSG:32617, max_depth 3.52m.

WMS layer: https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-job-0086-fix, publish result CONDITION_SUCCEEDED.

Screenshots in reports/inflight/job-0086-engine-20260608/evidence/:
- new_z12_dark_with_overlay.png: AFTER fix at z12 dark theme; deep blue at SW (Caloosahatchee / Cape Coral).
- old_z12_dark_with_overlay.png: BEFORE fix at z12 dark theme; deep blue at upper-north (bug confirmed).
- before_vs_after.png: side-by-side composite; overlays are vertically mirrored (Y-flip confirmed fixed).
- new_z12_city_with_overlay.png, new_z11_sw_with_overlay.png: additional z-level evidence.

Results: pass
