# Audit: postprocess_flood UX polish — rotation + transparency belt+suspenders + CRS_TAG_MISMATCH guard + auto-dispatch fix

**Job ID:** job-0071-engine-20260607, **Sprint:** sprint-10 Stage 1, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites (ALL APPROVED):**
- job-0058 (postprocess_flood squeeze + COG write); job-0063 (CRS-label fix); job-0070 (CRS regen + headline at correct location). Live raster now appears at Fort Myers but is rotated 90° CW and shows a faint blue tint over dry land.
- Research workflow `research-crs-mismatch-recurrence-20260607` (wf_6c8d62dc-2c1) — surfaced the recommended 6-LOC CRS_TAG_MISMATCH guard.

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-CE-4 (COG output format) — no edits
- `docs/srs/02-system-overview.md` Invariant 7 (no silent wrong answers) — the CRS guard's spiritual anchor
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` lines 140–340 — the bug sites
- `styles/continuous_flood_depth.qml` — the QML to edit
- `services/agent/src/grace2_agent/tools/publish_layer.py` — the auto-dispatch site
- `reports/complete/job-0070-engine-20260607/evidence/smoke_demo_envelope.json` — last known good envelope for shape reference

### Why this job exists

User feedback 2026-06-07 on the job-0070 Fort Myers headline screenshot: (a) flood layer is rotated 90° CW relative to the basemap; (b) the whole bounding box paints a faint blue tint instead of only inundation visible; (c) the agent-side `publish_layer` auto-dispatch fails with `JobsClient.run_job() got unexpected keyword argument 'overrides'` (OQ-70-AUTO-PUBLISH-DISPATCH), so today only manual `gcloud run jobs execute` publishes work. Plus the research workflow recommendation for a cheap structural guard against the CRS-mistag bug class.

### Scope — four tight changes in one commit

#### Change 1 — Rotation fix in `postprocess_flood.py`

Diagnose the axis-order assumption. The hmax variable has dims `(timemax, n, m)` per job-0058. After squeeze it's `(n, m)`. Current code (likely around `_extract_peak_depth_geotiff`) does:

```python
arr = np.asarray(depth.values, dtype="float32").squeeze()  # shape (n, m)
transform = rasterio.transform.from_bounds(x.min(), y.min(), x.max(), y.max(),
                                            arr.shape[-1], arr.shape[-2])
dst.write(arr_masked, 1)
```

`from_bounds(west, south, east, north, width, height)` — width is `arr.shape[-1]` (m), height is `arr.shape[-2]` (n).

For this to produce a north-up COG, the SFINCS netCDF convention must be `n = rows (y)` and `m = cols (x)`. Inspect the actual `x` and `y` coord variables in `sfincs_map.nc` to confirm the convention:

```
xr.open_dataset(<cog precursor>); ds["x"].dims, ds["y"].dims
```

If `ds["x"].dims == ("m",)` and `ds["y"].dims == ("n",)`, then n=y=rows ✓, m=x=cols ✓, and the code is correct — so the rotation isn't an axis swap and you should investigate further (maybe the SFINCS grid was set up rotated; the `setup_grid_from_region` `rotated=False` default should keep it north-up).

If `ds["x"].dims == ("n",)` and `ds["y"].dims == ("m",)`, then n=x=cols and m=y=rows — the code is computing transform with width=n and height=m WHEN it should be width=m and height=n. Fix: swap or transpose:

```python
arr = depth.values.squeeze()
if arr.shape != (height_expected, width_expected):
    arr = arr.T  # transpose to (y_rows, x_cols)
```

Adapt to whatever the inspection reveals. Capture the inspection result in the report.

If diagnosis shows no axis swap but rotation persists, it may be a SFINCS `setup_grid_from_region` rotation we picked up. In that case the COG transform needs an Affine including rotation. That's bigger scope — pause and surface as OQ-71-* rather than guess.

#### Change 2 — Transparency belt-and-suspenders

**Data side** (`postprocess_flood.py` around the `arr_masked = np.where(arr > 0.0, arr, np.nan)` line):
- Change threshold from `0.0` to `0.05` (5 cm physical-meaningful threshold — matches the existing `flooded_cell_count` reporting convention from job-0058's evidence)
- Add an explicit `NODATA_DEPTH_M = 0.05` constant at module top with a 1-line docstring citing the rationale

**Renderer side** (`styles/continuous_flood_depth.qml`):
- The lowest gradient stop (currently value 0 → light-blue at 82% opacity) needs its alpha set to 0 so value 0 (or any below-threshold) renders fully transparent
- Easiest: lower the lowest stop's value to 0.05 with alpha 0; the existing 0.4–3.5 m stops keep their colors
- OR: insert a discrete-mode rule `value < 0.05 → transparent`

Either approach is fine; pick whichever keeps the QML smaller and verify by re-opening it in QGIS or by hand inspecting it loads cleanly.

#### Change 3 — CRS_TAG_MISMATCH guard (research-workflow recommendation)

In `postprocess_flood.py` right after the `with rasterio.open(tmp_cog, "w", ...) as dst:` block writes the data (line ~306):

```python
# Belt-and-suspenders CRS verification (research-workflow recommendation
# 2026-06-07): catch any tag-vs-coords mismatch BEFORE the COG lands in
# the runs bucket. Closes the broader bug class around OQ-59 / OQ-69.
with rasterio.open(tmp_cog, "r") as verify:
    if str(verify.crs) != str(crs):
        raise PostprocessError(
            "CRS_TAG_MISMATCH",
            f"COG written with crs={crs!r} but rasterio read back {verify.crs!r}",
            details={"netcdf_path": str(netcdf_path)},
        )
    # Geographic CRS → coords are degrees (|x| ≤ 180); projected → coords are
    # meters or feet (|x| > 1000 for any non-degenerate extent).
    is_geographic = verify.crs.is_geographic
    bounds_max = max(abs(verify.bounds.left), abs(verify.bounds.right))
    if is_geographic and bounds_max > 360:
        raise PostprocessError(
            "CRS_TAG_MISMATCH",
            f"crs={crs!r} is geographic but bounds.left={verify.bounds.left} > 360",
            details={"netcdf_path": str(netcdf_path)},
        )
    if (not is_geographic) and bounds_max < 1000:
        raise PostprocessError(
            "CRS_TAG_MISMATCH",
            f"crs={crs!r} is projected but bounds.left={verify.bounds.left} < 1000",
            details={"netcdf_path": str(netcdf_path)},
        )
```

Add `CRS_TAG_MISMATCH` to the `PostprocessError` typed-error catalog docstring at the top of the file. Confirm the FR-FR-2 error-code routing dict (per sprint-8 FR-FR work) doesn't need an addition — the typed `PostprocessError` propagates up the chain.

#### Change 4 — `publish_layer.py` auto-dispatch fix (OQ-70-AUTO-PUBLISH-DISPATCH)

Find the `JobsClient.run_job(name=..., overrides=...)` call site in `tools/publish_layer.py`. The `overrides` kwarg signature mismatch suggests the google-cloud-run library version doesn't accept that keyword on `run_job`. The correct API for setting env vars at execution time is:

```python
from google.cloud.run_v2.types import RunJobRequest, Execution

request = RunJobRequest(
    name=job_path,
    overrides=RunJobRequest.Overrides(
        container_overrides=[
            RunJobRequest.Overrides.ContainerOverride(
                env=[
                    {"name": "OP", "value": "publish-raster"},
                    {"name": "QGS_URI", "value": qgs_uri},
                    # etc.
                ]
            )
        ]
    ),
)
operation = client.run_job(request=request)
```

(Verify against the installed `google-cloud-run` version in `.venv-agent`; the exact field names may differ — see `from google.cloud.run_v2.types import RunJobRequest; help(RunJobRequest.Overrides)`.)

The kickoff is purposely loose on the exact API shape — inspect the library and pick the working pattern. Verify by running the corresponding unit test (or adding one if it doesn't exist) that asserts `publish_layer` constructs a valid `RunJobRequest` without raising.

### Tests (≥4 new)

- Rotation test — assert COG written from a synthetic netCDF with known x/y coord orientations renders with the expected `transform.a > 0` (E-W positive pixel width) and `transform.e < 0` (N-S negative pixel height for north-up)
- Transparency data-side test — synthetic depth array with mixed `0.0`/`0.03`/`0.10`/`1.5` values → COG NaN cells exactly where depth < 0.05
- CRS_TAG_MISMATCH guard tests (3):
  1. Synthetic correct-CRS case → no raise
  2. Synthetic geographic-tag-with-projected-coords → raises CRS_TAG_MISMATCH
  3. Synthetic projected-tag-with-geographic-coords → raises CRS_TAG_MISMATCH
- publish_layer auto-dispatch test — assert the request shape matches `google.cloud.run_v2.RunJobRequest` schema and includes the expected env overrides

The QML edit isn't unit-testable directly but should be smoke-tested by loading it via PyQGIS in a Python REPL or by simple XML schema-validation.

### Live verification

DO NOT re-run the M5 smoke harness in this job — job-0072 will do the rebuild+re-publish+re-screenshot loop. This job just lands the code changes + tests. Acceptance is unit-test green + manual QML inspection + a NOTE in the report indicating the live verification gates on job-0072.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` (rotation + transparency data side + CRS guard)
- `styles/continuous_flood_depth.qml` (transparency renderer side)
- `styles/README.md` (small note if needed)
- `services/agent/src/grace2_agent/tools/publish_layer.py` (auto-dispatch overrides fix)
- `services/agent/tests/test_model_flood_scenario.py` (additive tests for rotation + transparency + CRS guard)
- `services/agent/tests/test_publish_layer.py` (additive test for the dispatch shape)
- `reports/inflight/job-0071-engine-20260607/`

### FROZEN

- `services/workers/pyqgis/` — worker code is job-0072's scope; do NOT touch
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py`
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — the workflow itself stays; only postprocess_flood + publish_layer change
- `services/agent/pyproject.toml`
- All web/, packages/contracts/, infra/, docs/srs/

### Acceptance criteria

- [ ] Rotation diagnosis + fix in `postprocess_flood`; test green
- [ ] Transparency belt-and-suspenders landed (data side `> 0.05` threshold + QML alpha=0 at bottom stop); tests green
- [ ] CRS_TAG_MISMATCH guard at line ~306; 3 tests green; `PostprocessError` catalog updated
- [ ] `publish_layer` `overrides` kwarg fix; test green
- [ ] Agent test suite stays 180+/180+
- [ ] Single commit; no edits to FROZEN paths
- [ ] Report notes "live verification gates on job-0072"
