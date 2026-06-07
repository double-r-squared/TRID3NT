# Report: OQ-59 CRS-label fix — postprocess_flood writes correct CRS tag on the COG

**Job ID:** job-0063-engine-20260607
**Sprint:** sprint-09 (Stage A, optional carry-forward)
**Specialist:** engine
**Task:** Fix OQ-59: postprocess_flood reads CRS from ds.attrs (always fires EPSG:3857 fallback) instead of the SFINCS crs data variable; add regression tests; verify COG CRS tag matches UTM-17N pixel coordinates.
**Status:** ready-for-audit

## Summary

Added `_read_crs_from_dataset(ds)` — a CF-convention-aware helper — to `postprocess_flood.py` that reads the CRS from the dataset's `crs` data variable before falling back to `ds.attrs`. Inspecting the real `sfincs_map.nc` confirmed the variable carries `attrs['epsg_code'] = 'EPSG:32617'`. Replaced the single buggy `ds.attrs.get("crs", "EPSG:3857")` call with the helper. Added three regression tests (OQ-59 tests 22/23/24) and confirmed the OQ-58 squeeze test still passes. Live verification against the real SFINCS netCDF shows the COG CRS tag flips from EPSG:3857 to EPSG:32617.

## Changes Made

- **File:** `services/agent/src/grace2_agent/workflows/postprocess_flood.py`
  - Added `_read_crs_from_dataset(ds: Any) -> str` immediately before `_extract_peak_depth_geotiff`. Tries `crs_var.attrs["epsg_code"]` first (SFINCS default — emits `"EPSG:32617"` with prefix), then `crs_var.attrs["crs_wkt"]` and `crs_var.attrs["spatial_ref"]` via pyproj, then fallback to `ds.attrs.get("crs", "EPSG:3857")` with logged warning.
  - Replaced `crs = ds.attrs.get("crs", "EPSG:3857")` with `crs = _read_crs_from_dataset(ds)`.

- **File:** `services/agent/tests/test_model_flood_scenario.py`
  - Added `_build_synthetic_sfincs_nc_oq59(tmp_path, ...)` helper for constructing synthetic sfincs_map.nc datasets.
  - Test 22 (`test_extract_peak_depth_geotiff_reads_crs_from_epsg_code_var`): crs variable with `epsg_code="EPSG:32617"` -> COG EPSG:32617.
  - Test 23 (`test_extract_peak_depth_geotiff_reads_crs_from_spatial_ref_wkt`): crs variable with WKT -> EPSG 32617 via pyproj.
  - Test 24 (`test_extract_peak_depth_geotiff_falls_back_to_attrs_crs_when_no_var`): no crs variable; ds.attrs["crs"]="EPSG:3857" -> fallback EPSG:3857.

## Decisions Made

- **Decision:** Read `epsg_code` as string (handles "EPSG:" prefix).
  - Rationale: real SFINCS file has `'epsg_code': 'EPSG:32617'` — already prefixed. Helper checks for prefix first, then tries int cast.
  - Alternatives: int cast only — rejected (ValueError on "EPSG:32617").

- **Decision:** Warning only when `"EPSG:3857"` fallback fires.
  - Rationale: must be visible in pipeline logs; should not fire when attrs-encoded CRS is intentional.

## Invariants Touched

- **Determinism boundary:** preserves — pure string read, no LLM.
- **Rendering through QGIS Server:** extends — correct CRS tag means QGIS Server can reproject COG to EPSG:4326 for WMS; old EPSG:3857 tag misplaced Fort Myers raster ~10 000 km.
- **Output format set is fixed:** preserves — COG format unchanged, only CRS metadata corrected.

## Open Questions

- OQ-59-FLOOD-COG-CRS-LABEL-VS-COORDS: resolved. SFINCS uses `crs_var.attrs["epsg_code"]` with string prefix.
- No new open questions.

## Dependencies and Impacts

- Depends on: job-0058 (APPROVED) — OQ-58 squeeze fix; regression test still passes.
- Affects: job-0060 (concurrent) — no conflict; postprocess_flood.py fix is additive.
- Downstream: QGIS Server will correctly interpret the COG CRS for WMS reprojection.

## Verification

### Tests run

```
PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q
1 failed, 169 passed, 4 warnings in 3.39s
```

The 1 failing test (`test_run_model_flood_scenario_triggers_loaded_layers_emit`) is pre-existing from job-0060 (PipelineEmitter constructor mismatch). Not related to this job.

OQ-58 + OQ-59 regression specifically:

```
.venv-agent/bin/python -m pytest services/agent/tests/ -v -k "squeeze or epsg_code or spatial_ref or crs_from"
test_extract_peak_depth_geotiff_squeezes_singleton_timemax_dim PASSED
test_extract_peak_depth_geotiff_reads_crs_from_epsg_code_var PASSED
test_extract_peak_depth_geotiff_reads_crs_from_spatial_ref_wkt PASSED
test_extract_peak_depth_geotiff_falls_back_to_attrs_crs_when_no_var PASSED
4 passed, 166 deselected in 1.38s
```

### Live E2E evidence

Full M5 chain hits GCS credential wall at LANDCOVER_READ_FAILED in local env (no ADC). CRS fix verified directly against `/tmp/grace2-m5-success/sfincs_map.nc` (real SFINCS output from job-0058's GCP run).

Verbatim verification:

```
=== Real sfincs_map.nc CRS variable ===
crs variable present: True
crs attrs: {'EPSG': '-', 'epsg_code': 'EPSG:32617'}
crs value: 32617

=== _read_crs_from_dataset result ===
CRS: EPSG:32617

=== COG written from real sfincs_map.nc ===
CRS tag: EPSG:32617
CRS EPSG: 32617
Bounds: BoundingBox(left=409109.0, bottom=2936568.0, right=425279.0, top=2952348.0)
Shape: 527 x 540
metrics[crs]: EPSG:32617
metrics[max_depth_m]: 3.515181064605713

BEFORE (job-0058): CRS tag = EPSG:3857 (Web Mercator — WRONG)
AFTER  (job-0063): CRS tag = EPSG:32617 — correct for UTM 17N
```

Evidence JSON: `reports/inflight/job-0063-engine-20260607/evidence/oq59_crs_verification.json`

### Before / After

| | Before (job-0058) | After (job-0063) |
|---|---|---|
| COG CRS tag | EPSG:3857 (Web Mercator) | EPSG:32617 (UTM 17N) |
| Pixel coordinates | UTM 17N | UTM 17N |
| Tag/coords match | NO | YES |
| metrics["crs"] | "EPSG:3857" | "EPSG:32617" |

### Bug class scan

`grep -rn 'ds\.attrs\.get.*crs' services/` — only one code call site remains (line 188 in the helper fallback, intentional). No other instances of the bug.

### Results: PASS — CRS tag now matches UTM-17N pixel coordinates
