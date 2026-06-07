# Audit: OQ-59 CRS-label fix — postprocess_flood writes correct CRS tag on the COG

**Job ID:** job-0063-engine-20260607, **Sprint:** sprint-09 (Stage A, optional carry-forward), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- job-0058 (engine, APPROVED) — surfaced OQ-59-FLOOD-COG-CRS-LABEL-VS-COORDS in retrospective.

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-CE-4 (output format — COGs include CRS + units + provenance)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` lines 174-244 — the COG write path
- `reports/complete/job-0058-engine-20260607/report.md` for the bug context

### Why this job exists

In `postprocess_flood.py:212` the COG's CRS is computed as:
```python
crs = ds.attrs.get("crs", "EPSG:3857")
```

The SFINCS-emitted `sfincs_map.nc` stores its CRS in a data variable named `crs` (not in `.attrs`). The `.attrs.get("crs", ...)` lookup returns the `"EPSG:3857"` default. Meanwhile, the coordinates themselves are written in UTM 17N (EPSG:32617) from the SFINCS grid. Result: the COG metadata says EPSG:3857 (Web Mercator) but the pixel coordinates are UTM 17N — a label/coords mismatch that misplaces the raster by thousands of km in any GIS that respects the tag.

The data is correct; only the CRS tag is wrong. Tiny fix.

### Scope

1. **`services/agent/src/grace2_agent/workflows/postprocess_flood.py`** — read the CRS from the dataset's `crs` variable (not `.attrs`), with `EPSG:3857` as a last-resort fallback. Approach options (pick simpler one):
   - **Option A:** `crs_var = ds["crs"]` exists; extract via `pyproj.CRS.from_wkt(crs_var.attrs["spatial_ref"])` or similar — verify how SFINCS encodes the CRS in the netCDF (check job-0058's smoke_demo_envelope.json + the netCDF itself if needed).
   - **Option B:** If SFINCS stores the EPSG code as an integer attribute on the `crs` variable, use `f"EPSG:{int(ds['crs'].attrs['epsg_code'])}"` directly.
   - Fallback: keep `EPSG:3857` only if no CRS info is available, with a logged warning.

2. **Tests** in `services/agent/tests/test_model_flood_scenario.py` (or a postprocess-specific test file — check what exists from job-0058):
   - Add ≥1 test that constructs a synthetic xarray Dataset with a `crs` data variable carrying a known CRS (e.g., EPSG:32617) and asserts the resulting COG's `rasterio.open(path).crs.to_string() == "EPSG:32617"`.
   - The squeeze regression test from job-0058 stays.

3. **Verify** by re-running the M5 smoke harness (`reports/complete/job-0058-engine-20260607/evidence/smoke_demo.py` copied to `reports/inflight/job-0063-engine-20260607/evidence/`) — the new COG's tag should now match its coordinates. Open with rasterio and confirm.

### File ownership (exclusive)
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py` — the CRS-read block at line ~212 only
- `services/agent/tests/test_model_flood_scenario.py` (or new test file) — additive
- `reports/inflight/job-0063-engine-20260607/`

### FROZEN
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py`
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` (concurrent job-0060 owns)
- `services/agent/pyproject.toml`
- `services/workers/sfincs/entrypoint.py`
- All other workflows/* and tools/*

### Acceptance criteria
- [ ] CRS read from the dataset's `crs` variable (not `.attrs`) with EPSG fallback
- [ ] ≥1 regression test guards the CRS-tag emission
- [ ] Live M5 re-run produces a COG whose `rasterio.crs` matches the source coordinates (e.g., EPSG:32617 for Fort Myers UTM 17N)
- [ ] Old squeeze test from job-0058 still passes
- [ ] No edits to FROZEN paths
- [ ] Single commit
