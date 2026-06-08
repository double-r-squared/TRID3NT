# Audit: `postprocess_flood` Y-axis flip fix + regenerate + republish

**Job ID:** job-0086-engine-20260608, **Sprint:** sprint-11 Stage 1 capstone (post-OQ-76 real fix), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites (ALL APPROVED):**
- job-0063 (CRS-label fix): `_read_crs_from_dataset` handles CF-convention encodings
- job-0070 (regenerated COG with CRS fix applied)
- job-0071 (postprocess_flood UX polish + CRS guard + 0.05m wet threshold)
- job-0075 (latest published COG + WMS layer `flood-depth-job-0075-demo`)
- job-0078 (OQ-76 client-side investigation — ruled out URL/MapLibre bugs; raster-resampling: nearest now in)

## Why this job exists

**The user-observed flood-overlay alignment problem is REAL** and has a concrete root cause that job-0078's Opus diagnostic did not catch:

Orchestrator local diagnosis (2026-06-08, no agent):
1. Downloaded `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak.tif` via rasterio → COG geotransform is **clean** (a=+29.94, e=-29.94, no rotation, bounds = Fort Myers WGS84 (-81.91, 26.55, -81.75, 26.69))
2. Rendered the raw COG to PNG bypassing QGIS Server → exhibits the same spatial pattern as the WMS-served PNG (so QGIS Server is **innocent** — it faithfully serves what's in the COG)
3. Downloaded `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/sfincs_map.nc` via fsspec → SFINCS netCDF stores `y` coordinate **ascending along rows**: `y[0, 0] = 2,936,568` (south UTM) and `y[-1, 0] = 2,952,348` (north UTM)
4. `postprocess_flood._extract_peak_depth_geotiff` (services/agent/src/grace2_agent/workflows/postprocess_flood.py:333-338) calls `rasterio.transform.from_bounds(west, south, east, north, w, h)` which produces a **standard north-up** geotransform (row 0 = north). Then it writes the SFINCS `arr_masked` **as-is** at line 357. **Row 0 of SFINCS data = south, but COG declares row 0 = north → Y-axis flip.**

The user sees this as "mirrored on the X axis" (reflected across the horizontal axis = top/bottom swapped = row reversal). Symptomatically: the river-mouth + bay storm surge (geographically at the SOUTH of the bbox) paints onto the NORTH of the overlay rectangle.

**Why prior jobs missed it:**
- job-0070 only fixed the CRS *label* mismatch — never verified pixel content against geography
- job-0071's "rotation fix" (postprocess_flood.py:276-301) only handles `(m, n)` vs `(n, m)` dim-name transpose; does NOT check the *direction* of `y` along its axis
- job-0078's URL-consistency proof was vacuous against in-COG mirroring: server and client both honor a faithfully-served-but-internally-mirrored COG identically

## Scope — small, surgical

### Part 1 — Y-orientation guard in `postprocess_flood._extract_peak_depth_geotiff`

File: `services/agent/src/grace2_agent/workflows/postprocess_flood.py`

Insert **immediately before the COG write** (after the existing line 333-340 block reads `_x` / `_y` and computes transform):

```python
# --- Y-orientation guard (job-0086) ---
# SFINCS often emits y ascending along rows (row 0 = south). COG built via
# rasterio.transform.from_bounds(...) declares row 0 = north. If we write
# arr as-is into that transform, the COG is internally Y-flipped: deep-flood
# pixels (at the SOUTH river mouth) paint onto the NORTH of the bbox.
# Detect direction along the row axis and flip BOTH arr + arr_masked.
try:
    _y_vals = ds["y"].values
    if _y_vals.ndim == 2:
        y_ascends_along_rows = bool(_y_vals[0, 0] < _y_vals[-1, 0])
    else:
        y_ascends_along_rows = bool(_y_vals[0] < _y_vals[-1])
    if y_ascends_along_rows:
        logger.info(
            "postprocess_flood: flipping rows — SFINCS y ascends along rows "
            "(row 0 = south, %.2f → %.2f); COG expects row 0 = north. "
            "Y-axis flip applied (job-0086).",
            float(_y_vals.flat[0]), float(_y_vals.flat[-1]),
        )
        arr = arr[::-1, :]
        arr_masked = arr_masked[::-1, :]
except Exception:  # noqa: BLE001 — defensive; bad y → identity, no harm
    logger.warning("postprocess_flood: y-orientation probe failed; not flipping")
```

Also add an X-orientation guard analogously (cheap, future-proofs against curvilinear grids where x might also be descending) — verify by checking `_x_vals` along the column axis. **This is belt-and-suspenders; do NOT change x behavior for any case where x is already ascending.** Match the y guard pattern exactly.

Verify metrics stats (`max_depth_m`, `mean_depth_m`, `p95_depth_m`, `flooded_cell_count`) are unchanged by the flip — these are aggregate over the array and must be flip-invariant. Spot-check the test asserts equal values pre/post fix.

### Part 2 — Regression test

NEW or extended file: `services/agent/tests/workflows/test_postprocess_flood.py` (or wherever existing `test_postprocess_flood*.py` lives — DO NOT create a parallel file).

Build a synthetic netCDF in pytest with:
- `x`: 1D ascending `[0, 1, 2, 3, 4]` (5 cols)
- `y`: 1D ascending `[0, 10, 20, 30]` (4 rows, **south at index 0**)
- `hmax`: shape `(1, 4, 5)` with an **asymmetric pattern** (e.g. high values at low-y row index 0; zero at high-y row index 3) so the flip is detectable from pixel content, not just metadata
- `crs` data variable encoding "EPSG:32617"

Assert after `_extract_peak_depth_geotiff`:
- The resulting GeoTIFF has high values at the **south edge** (row index `height-1`, since COG row 0 = north)
- Aggregate metrics (`max_depth_m`) unchanged vs pre-flip raw array
- A second test with `y` already descending verifies the guard is **idempotent** (no flip applied)

Run the full agent suite: `.venv-agent/bin/pytest services/agent/tests/ -q` — target 0 regressions.

### Part 3 — Re-generate Fort Myers COG + republish

Use the EXISTING `sfincs_map.nc` at `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/sfincs_map.nc`. **Do NOT re-run SFINCS** — re-running the solver is overkill; we only need to re-extract+rewrite the COG with the fix in place.

Steps:
1. Stage the netCDF locally (fsspec download)
2. Run `_extract_peak_depth_geotiff(...)` from the **fixed** module on it
3. Upload the resulting COG to a NEW path: `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak_0086.tif` (do NOT clobber the 0075 path — user may want to compare side-by-side)
4. Invoke `publish_layer` atomic tool to register a new WMS layer: `flood-depth-job-0086-fix` on the same QGIS Server project
5. Capture WMS endpoint URL for the new layer

### Part 4 — Live verification screenshots

Use `/tmp/grace2_zoomout_probe.py` as starting reference (orchestrator wrote this 2026-06-08; live in /tmp). Adapt to:
- Inject session-state with the NEW `flood-depth-job-0086-fix` layer at z11
- Dark theme (per orchestrator-direct probe convention)
- Capture with-overlay + basemap-only pair at z11_bay
- Stash all evidence to `reports/inflight/job-0086-engine-20260608/evidence/`

**Acceptance — pixel-level, not "screenshot captured"**:
- Visually verify in the z11 screenshot: the **wettest flood pixels** (depths > 1.5m) now appear at the **river mouth + San Carlos Bay area** in the SOUTHWEST of the overlay rectangle (where they geographically belong), NOT the northwest
- Side-by-side comparison: produce `evidence/before_vs_after.png` showing the OLD `flood-depth-job-0075-demo` layer next to the NEW `flood-depth-job-0086-fix` layer at identical camera state — they should be VERTICALLY MIRRORED

### File ownership (exclusive)

- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` (Y-orientation guard only, ~15 lines)
- `services/agent/tests/workflows/test_postprocess_flood.py` (additive — find existing or create)
- `reports/inflight/job-0086-engine-20260608/`

### FROZEN

- ALL other workflows/* (`sfincs_builder.py`, `model_flood_scenario.py`, `run_solver.py`, etc.)
- ALL tools/* (including the 7 just-landed engine tools from jobs 0079-0085)
- `services/workers/pyqgis/**` — the pyqgis worker is the publish path; do NOT modify its code (it can still be invoked to publish the new layer)
- `web/**` — job-0078's `raster-resampling: nearest` fix stays; no web changes here
- `packages/contracts/**`, `infra/**`, `docs/srs/**`, `styles/**`, `reports/complete/**`

### Acceptance criteria

- [ ] Y-orientation guard lands in `postprocess_flood.py` with the helper logging line
- [ ] X-orientation guard added analogously (belt-and-suspenders)
- [ ] Regression test on synthetic netCDF asserts post-fix orientation + flip-invariant metrics + idempotence on already-descending y
- [ ] Full agent suite `pytest -q` shows 0 new failures (target: 275+ passed matching current baseline)
- [ ] New COG published at `gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak_0086.tif`
- [ ] New WMS layer `flood-depth-job-0086-fix` queryable via QGIS Server
- [ ] z11 dark-theme with-overlay + basemap-only screenshots show wettest pixels at **river mouth + bay (southwest)** — not northwest
- [ ] `evidence/before_vs_after.png` side-by-side proving vertical mirror between OLD and NEW
- [ ] No FROZEN edits
- [ ] Single commit

### Honest disclosure

If the SFINCS netCDF turns out to have y *descending* (north at row 0) AND I (orchestrator) misread the probe output, the guard becomes a no-op and the fix doesn't change rendered pixels. Verify by logging `_y_vals[0,0]` vs `_y_vals[-1,0]` and reporting both. If guard does nothing AND the rendered overlay is still mirrored, escalate as OQ-86-Y-ORIENTATION-WRONG-DIAGNOSIS and STOP — do NOT chase the bug into x-axis hypotheses without re-anchoring on data.

If publish_layer or pyqgis worker dispatch fails (auth / quota / cold start), surface as OQ-86-PUBLISH-INFRA-* and stop — the code fix + test alone is still bankable; republish is a follow-up.
