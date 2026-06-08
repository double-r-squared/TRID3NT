# Report: `compute_colored_relief` atomic tool

**Job ID:** job-0080-engine-20260608
**Sprint:** sprint-11 Stage 1 parallel
**Specialist:** engine
**Task:** NEW `tools/compute_colored_relief.py` atomic tool wrapping `gdaldem color-relief`. 4 ramp presets (terrain / elevation_blue_green / grayscale / viridis). Cache key on (dem_uri, ramp). FR-DC integration (cacheable=True, ttl_class="static-30d", source_class="colored_relief"). Returns LayerURI.
**Status:** ready-for-audit

## Summary

`compute_colored_relief` is a new atomic tool wrapping `gdaldem color-relief` with four built-in elevation ramp presets (terrain, elevation_blue_green, grayscale, viridis). The tool integrates with the FR-DC-3 cache shim via `read_through` (TTL static-30d), returns a `LayerURI` pointing at a cached 4-band RGBA GeoTIFF, and is registered in the tool registry at startup. Live verification on a synthetic 32x32 DEM with the `grace2` conda env's `gdaldem` confirmed each preset produces a 4-band RGBA output. The `--startup-only` flag shows `compute_colored_relief` in the 19-tool registry.

## Changes Made

- **File:** `services/agent/src/grace2_agent/tools/compute_colored_relief.py` (NEW)
  - `_RAMPS` dict with four inline ramp definitions, each with 8-11 control points covering -500m to 9000m.
  - `_write_ramp_file()` writes a named ramp to a temp file in `gdaldem color-relief` CSV format.
  - `_run_colored_relief()` routes GCS URIs through GDAL's `/vsigs/` virtual FS, runs `gdaldem color-relief -alpha -compute_edges`, returns output bytes.
  - `compute_colored_relief()` — registered atomic tool with FR-TA-3-complete docstring.

- **File:** `services/agent/src/grace2_agent/tools/__init__.py`
  - Added: `from . import compute_colored_relief` (1 line)

- **File:** `services/agent/src/grace2_agent/main.py`
  - Added: `from .tools import compute_colored_relief` in `_import_tools_registry()` (1 line)

- **File:** `services/agent/tests/test_compute_colored_relief.py` (NEW)
  - 19 tests: 15 pass unconditionally, 4 skipped when `gdaldem` not on PATH.

## Decisions Made

- **Ramp definitions inline (not `.txt` files):** Self-contained, no file I/O at import, kickoff explicitly allowed it. `styles/ramps/` option deferred.
- **4-band RGBA via `-alpha`:** Transparent no-data pixels are better for flood overlay compositing than painting with the lowest-elevation colour. Kickoff said "3- or 4-band"; RGBA is strictly better.
- **`style_preset="continuous_dem"` placeholder:** No `colored_relief.qml` preset exists yet. Nearest available preset used; surfaced as OQ-80-COLORED-RELIEF-STYLE-PRESET.
- **`role="context"`:** Colored relief is a terrain context layer beneath flood data, not a primary hazard result.

## Invariants Touched

- Determinism boundary: preserves — typed LayerURI, no prose-embedded metrics.
- Engine registration, not modification: preserves — `@register_tool` only.
- Rendering through QGIS Server: preserves — produces a CRS-tagged GeoTIFF; rendering is server-side.
- Metadata-payload pattern: preserves — GCS writes via `read_through` only.
- Minimal parameter surface: preserves — `(dem_uri, ramp)` are both irreducible.

## Open Questions

- **OQ-80-COLORED-RELIEF-STYLE-PRESET:** `style_preset="continuous_dem"` is a placeholder. A dedicated `colored_relief.qml` (or per-ramp variants) should be authored in a follow-up job. TENTATIVE: acceptable for sprint-11.
- **OQ-80-VSIGS-AUTH-IN-PROD:** `/vsigs/` requires ADC. Dev sessions without gcloud ADC get `OSError: Project was not passed`. Consistent with `fetch_dem` / `publish_layer`. Production Cloud Run service accounts have ADC. No action needed.
- **OQ-80-GDALDEM-NOT-ON-VENV-PATH:** `gdaldem` is in the `grace2` conda env but not `venv-agent`. The 4 synthetic-DEM tests skip cleanly. Proposed follow-up: add `GRACE2_GDALDEM_BIN` env-var override (like `GRACE2_QGIS_PROCESS_BIN`) so CI can pin the binary. TENTATIVE: not in this job scope.

## Dependencies and Impacts

- Depends on: job-0033 (fetch_dem pattern), job-0039 (cache shim), job-0062 (publish_layer pattern).
- Parallel with: job-0079 (compute_hillshade), job-0081 (compute_slope) — both also add 1-line registrations; no file conflicts observed.
- Feeds into: future `publish_terrain_composite` (OQ-79) as the colored-relief side of the hillshade+colorramp terrain stack.

## Verification

**Tests:** 202 passed, 5 skipped, 0 failed (full suite). 15 new tests pass; 4 skipped (gdaldem not on venv-agent PATH).

**`--startup-only` live run:**
```
2026-06-08 00:57:42,789 INFO grace2_agent.main tool registry loaded: 19 tool(s): ['catalog_fetch', 'catalog_search', 'compute_colored_relief', 'compute_slope', 'describe_qgis_algorithm', 'fetch_buildings', 'fetch_dem', 'fetch_landcover', 'fetch_population', 'fetch_river_geometry', 'geocode_location', 'list_qgis_algorithms', 'lookup_precip_return_period', 'mongo_query', 'publish_layer', 'qgis_process', 'run_model_flood_scenario', 'run_solver', 'wait_for_completion']
```
`compute_colored_relief` visible at startup (19 tools, up from 17 before jobs 0079-0081 parallel wave).

**Synthetic DEM live run (PATH includes grace2 gdaldem):**
```
  ramp='terrain': OK - 4607 bytes, 4 bands, 32x32, CRS=EPSG:4326
  ramp='elevation_blue_green': OK - 4607 bytes, 4 bands, 32x32, CRS=EPSG:4326
  ramp='grayscale': OK - 4607 bytes, 4 bands, 32x32, CRS=EPSG:4326
  ramp='viridis': OK - 4607 bytes, 4 bands, 32x32, CRS=EPSG:4326
```
All four ramp presets produce 4-band RGBA output from a 32x32 synthetic DEM (0-900m gradient, EPSG:4326).

**GCS live run:** Attempted with Fort Myers DEM (`gs://grace-2-hazard-prod-cache/cache/static-30d/dem/87ba00463af0275d02115f7463afe6e9.tif`). Failed: `OSError: Project was not passed` — ADC not configured in this dev session. Consistent with all other GCS-bound tools. Verification: **qualified** (GCS auth unavailable; tool logic fully verified via synthetic DEM + cache-shim tests).
