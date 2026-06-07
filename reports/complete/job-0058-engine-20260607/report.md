# Report: postprocess_flood squeeze singleton timemax dim before COG write (OQ-58)

**Job ID:** job-0058-engine-20260607
**Sprint:** sprint-08
**Specialist:** engine
**Task:** Squeeze singleton `timemax` dim from hmax array before COG write; add regression test; re-run M5 smoke to SUCCESS.
**Status:** ready-for-audit

## Summary

Applied a 12-line fix to `postprocess_flood.py` that squeezes any singleton leading dimensions from the extracted depth array before the rasterio COG write. HydroMT-SFINCS 1.2.2 emits `hmax` with shape `(timemax=1, n=527, m=540)`; `rasterio.write(arr, 1)` expects exactly 2D. After the fix the full M5 chain runs to `outcome=SUCCESS` with the COG uploaded at `gs://grace-2-hazard-prod-runs/01KTHTFCV5E588A293N0JFQTZH/flood_depth_peak.tif`. All 165 tests pass (164 baseline + 1 new regression guard).

## Changes Made

- **File:** `services/agent/src/grace2_agent/workflows/postprocess_flood.py`
  - Added `RUN_OUTPUT_UNEXPECTED_SHAPE` to the `PostprocessError` docstring error-code list.
  - Added squeeze block after `arr = np.asarray(depth.values, dtype="float32")`: if `arr.ndim > 2`, calls `np.squeeze(arr)` and raises `PostprocessError("RUN_OUTPUT_UNEXPECTED_SHAPE")` if the result is still not 2D. Applied at the single convergence point after all three depth-extraction branches (hmax, zsmax-zb, zs.max-zb) so the contract is uniform.

- **File:** `services/agent/tests/test_model_flood_scenario.py`
  - Added Test 20 (`test_extract_peak_depth_geotiff_squeezes_singleton_timemax_dim`): constructs a synthetic `sfincs_map.nc` with `hmax` shape `(1, 8, 8)` using xarray, calls `_extract_peak_depth_geotiff` directly, asserts no exception, asserts output COG has 1 band + shape `(8, 8)`, and validates metrics.

## Decisions Made

- **Decision:** Applied squeeze at the single convergence point after all three branches merge to `arr`.
  - Rationale: DRY; covers all extraction paths uniformly.
- **Decision:** Guard `if arr.ndim > 2` rather than unconditionally squeezing.
  - Rationale: explicit no-op when data is already 2D.

## Invariants Touched

- **Deterministic workflows:** preserves — pure NumPy transform.
- **Tier separation:** preserves — COG write stays in agent service layer.
- **Rendering through QGIS Server:** preserves — single-band float32 NaN-nodata COG contract unchanged.

## Open Questions

None.

## Dependencies and Impacts

- Depends on: job-0057 (APPROVED)
- Affects: None — postprocess_flood is a leaf; downstream AssessmentEnvelope assembly unchanged.

## Verification

### Tests run

```
PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q
165 passed, 4 warnings in 3.11s
```

### Live E2E evidence

M5 smoke run log: `reports/inflight/job-0058-engine-20260607/evidence/smoke_demo_log.txt`
Envelope JSON: `reports/inflight/job-0058-engine-20260607/evidence/smoke_demo_envelope.json`

Key log lines (verbatim):
```
INFO grace2_agent.workflows.postprocess_flood uploaded flood-depth COG to gs://grace-2-hazard-prod-runs/01KTHTFCV5E588A293N0JFQTZH/flood_depth_peak.tif
INFO grace2_agent.workflows.model_flood_scenario model_flood_scenario complete envelope_id=01KTHTZVRF1JTFHAH6R5BDYCQ1 run_ids=['01KTHTFCV5E588A293N0JFQTZH'] layers=1
INFO smoke_demo outcome=SUCCESS solver_version=sfincs-v2.3.3 layers=1 elapsed=583.83s
```

AssessmentEnvelope:
- outcome: SUCCESS
- envelope_id: 01KTHTZVRF1JTFHAH6R5BDYCQ1
- layer_uris: ["gs://grace-2-hazard-prod-runs/01KTHTFCV5E588A293N0JFQTZH/flood_depth_peak.tif"]
- flood_max_depth_m: 3.515
- flood_solver_version: sfincs-v2.3.3
- flood_grid_resolution_m: 30.0
- forcing_type: pluvial_synthetic (NOAA Atlas 14 100-yr/24-hr)

rasterio.open verification (live GCS read):
```
URI: gs://grace-2-hazard-prod-runs/01KTHTFCV5E588A293N0JFQTZH/flood_depth_peak.tif
CRS: EPSG:3857
Bounds: BoundingBox(left=409109.0, bottom=2936568.0, right=425279.0, top=2952348.0)
Shape: 527 x 540
Dtype: float32  NoData: nan  Band count: 1
Flooded cells: 284580  Max depth: 3.515 m  Mean depth: 0.338 m
```

### Results: PASS — SUCCESS (M5 headline complete)

---

## SCREENSHOT MOMENT

The production COG path is now whole:

```
gs://grace-2-hazard-prod-runs/01KTHTFCV5E588A293N0JFQTZH/flood_depth_peak.tif
```

CRS: EPSG:3857 (Web Mercator / QGIS Server compatible), 527x540 px, float32, NaN nodata, 1 band.
Max depth 3.52 m, 284,580 flooded cells at Fort Myers FL.
Style preset: continuous_flood_depth.
The orchestrator can render this COG via Playwright + QGIS Server WMS path.
