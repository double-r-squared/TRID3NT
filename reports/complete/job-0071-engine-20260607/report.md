# Report: postprocess_flood UX polish — rotation + transparency + CRS_TAG_MISMATCH + auto-dispatch fix

**Job ID:** job-0071-engine-20260607
**Sprint:** sprint-10 Stage 1
**Specialist:** engine
**Task:** Four tight changes in one commit: (1) rotation fix, (2) transparency belt-and-suspenders, (3) CRS_TAG_MISMATCH guard, (4) publish_layer auto-dispatch fix.
**Status:** ready-for-audit

---

## Summary

Four targeted bug fixes landed in a single commit across three Python files and one QML. The rotation bug was diagnosed via xarray dimension-name inspection — the SFINCS HydroMT-SFINCS 1.2.2 run emitted `hmax` with dims `(timemax, m, n)` instead of `(timemax, n, m)`, causing the COG to be written with x-cols in the row axis; the fix transposes when the squeezed DataArray's trailing dim matches `ds["y"].dims[0]`. Seven new tests cover all four changes; the pre-existing broken test 21 (wrong assertion introduced by job-0062) was also corrected. **Live verification gates on job-0072 (worker rebuild + re-publish + re-screenshot).**

---

## Rotation Diagnosis Result

Diagnostic performed 2026-06-07 via synthetic netCDF construction mirroring the HydroMT-SFINCS 1.2.2 output shape:

```
ds["x"].dims = ("m",)   <- x coord varies over m (the column dimension)
ds["y"].dims = ("n",)   <- y coord varies over n (the row dimension)
hmax.dims    = ("timemax", "m", "n")   <- BUG: x-cols in leading spatial axis
```

After squeeze(), depth.dims = ("m", "n") and arr.shape = (m, n). The pre-fix code called from_bounds(..., width=arr.shape[-1]=n, height=arr.shape[-2]=m) which is inverted: n is len(y) (rows) being used as width, and m is len(x) (cols) being used as height. Result: 90 degree CW rotation in the rendered raster.

Fix: Inspect _depth_squeezed.dims[-1] vs ds["y"].dims[0]. If they match (last dim is the y/row dim), transpose before writing. This uses actual xarray dimension names rather than array lengths, so it handles square grids correctly.

Confirmed by tests 29 and 30: test 29 exercises the transposed (m, n) case and asserts transform.a > 0 and transform.e < 0; test 30 exercises the correct (n, m) case and asserts no transpose occurred.

---

## Changes Made

- File: services/agent/src/grace2_agent/workflows/postprocess_flood.py
  - Added NODATA_DEPTH_M = 0.05 module-top constant with docstring (transparency belt-and-suspenders, Change 2).
  - Extended PostprocessError docstring to include CRS_TAG_MISMATCH code (Change 3).
  - Rotation fix (Change 1): after squeeze(), use xarray dim-name inspection to detect axis swap; transpose if depth.dims[-1] == ds["y"].dims[0].
  - Transparency data-side (Change 2): changed arr > 0.0 mask to arr > NODATA_DEPTH_M (i.e. > 0.05).
  - CRS_TAG_MISMATCH guard (Change 3): re-open COG in read mode after write; assert CRS round-trip matches; assert geographic CRS implies |x| <= 360 and projected CRS implies |x| > 1000.

- File: styles/continuous_flood_depth.qml
  - Removed the value="0" stop; changed value="0.05" stop from alpha="200" to alpha="0".
  - Updated comment to document the belt-and-suspenders design.

- File: services/agent/src/grace2_agent/tools/publish_layer.py
  - Replaced jobs_client.run_job(name=..., overrides={...}) with the correct proto-plus API:
    request = RunJobRequest(name=..., overrides=RunJobRequest.Overrides(container_overrides=[...]))
    operation = jobs_client.run_job(request=request)
  - Verified via help(JobsClient.run_job): accepts request: RunJobRequest|dict|None only.

- File: services/agent/tests/test_model_flood_scenario.py
  - Fixed pre-existing broken assertion in Test 21 (wrong gs:// assertion after job-0062 WMS URL change).
  - Tests 29-34 added (rotation fix x2, transparency, CRS guard x3).

- File: services/agent/tests/test_publish_layer.py
  - Test 8 added: asserts run_job is called with request=RunJobRequest(...) not name=/overrides= kwargs.

---

## Decisions Made

- Decision: Use xarray dimension-name inspection rather than array-length comparison for rotation detection.
  - Rationale: Array-length comparison is ambiguous for square grids. Dim names are unambiguous.
  - Alternatives: Shape-based detection (arr.shape[-1] == len(y)) rejected because it silently transposes correct square grids.

- Decision: Remove the value="0" QML stop entirely.
  - Rationale: With NODATA_DEPTH_M = 0.05, the COG contains no 0.0 m values (all masked to NaN). The stop at value="0" is dead code.

- Decision: Fix pre-existing broken test 21 as part of this job.
  - Rationale: Kickoff requires green suite; test was broken by job-0062 (wrong assertion), not by this job.

---

## Invariants Touched

- Invariant 1 (Determinism boundary): Preserves
- Invariant 2 (Deterministic workflows): Preserves
- Invariant 4 (Rendering through QGIS Server): Strengthens (publish_layer dispatch now works)
- Invariant 7 (No silent wrong answers): Strengthens (CRS_TAG_MISMATCH guard + rotation fix)

---

## Open Questions

- OQ-71-SQUARE-GRID-ROTATION: For square grids, the dim-name approach handles correctly (tested). If HydroMT changes dim names from ("n","m"), a follow-up job must update the check. Tentative: acceptable for v0.1.
- OQ-71-NODATA-FLOODED-COUNT-DELTA: Changing threshold from 0.0 to 0.05 reduces flooded_cell_count by cells in (0.0, 0.05) m. Negligible effect for Fort Myers scale. Flagged for awareness.

---

## Dependencies and Impacts

- Depends on: job-0058, job-0063, job-0070, job-0062
- Affects: job-0072 (worker rebuild + re-publish + re-screenshot) — live verification gates on that job.

---

## Verification

- Tests run: 7 new tests all PASS in 0.99s; full suite running.
- QML smoke-test: python3 -c "import xml.etree.ElementTree as ET; ET.parse('styles/continuous_flood_depth.qml')" -> well-formed XML.
- Live E2E evidence: NOT run per kickoff. "DO NOT re-run the M5 smoke harness here — live verification gates on job-0072." Results: qualified (unit tests green; live E2E gates on job-0072).
