# Report: `compute_zonal_statistics` atomic tool (hazard-analysis primitive)

**Job ID:** job-0083-engine-20260608
**Sprint:** sprint-11 Stage 1 parallel
**Specialist:** engine
**Task:** NEW `tools/compute_zonal_statistics.py` — foundational hazard-analysis primitive returning per-zone + aggregate stats from a value raster intersected with a raster mask or vector polygons.
**Status:** ready-for-audit

## Summary

Implemented `compute_zonal_statistics` as a new atomic tool registered via `@register_tool`. It auto-detects zone input type (raster vs vector by extension, with rasterio fallback), handles raster-zone reprojection when grids differ, and supports all 10 requested statistics. The tool is cache-integrated via the `read_through` shim, storing results as JSON at `cache/dynamic-1h/zonal_statistics/<key>.json`. 11 unit tests pass; live verification against job-0075's flood COG produced real exposure numbers (48,155 pixels in zone, mean depth 0.37m, max 1.94m, 95th pct 0.89m).

## Changes Made

- **`services/agent/src/grace2_agent/tools/compute_zonal_statistics.py`** (NEW)
  - `ZonalStatisticsError` with typed error codes
  - `_compute_stats()`: all 10 statistics including percentiles
  - `_detect_zone_type()`: extension-based with rasterio fallback
  - `_zonal_stats_raster_zone()`: `rasterio.warp.reproject` to align grids when dimensions/CRS differ
  - `_zonal_stats_vector_zone()`: per-polygon rasterization via `rasterio.features.rasterize`; ID from feature `id` property or sequential index
  - `_read_vector_features()`: GeoJSON direct + OGR/fiona fallback
  - `_derive_cache_key()`: SHA-256 of canonical JSON of all 5 parameters
  - `compute_zonal_statistics()`: registered tool, cache via `read_through`, result as JSON bytes
  - `_materialize_uri()`: GCS download to temp; local paths pass through
- **`services/agent/src/grace2_agent/tools/__init__.py`** — 1 line added
- **`services/agent/src/grace2_agent/main.py`** — 1 line added
- **`services/agent/tests/test_compute_zonal_statistics.py`** (NEW, 11 tests)
- **`services/agent/pyproject.toml`** — NOT modified (rasterstats not added)

## Decisions Made

- **No rasterstats dependency**: Rolling own with rasterio + numpy is sufficient and tested. `rasterstats` would be cleaner for vector zones but adds an unjustified dependency.
- **Raster zone reprojection**: `rasterio.warp.reproject` (nearest-neighbor) auto-triggers when zone grid differs from value grid. Required by real-world usage (confirmed in live test: COG 540×527 vs synthetic mask 135×132).
- **Cache key design**: SHA-256 of canonical JSON of all 5 parameters as `source_id` suffix + `params` entry, so `read_through`'s internal key is also parameter-stable.
- **NaN nodata**: Explicit `math.isnan()` guard added because flood COG uses `nodata=nan`. `val_data != nan` is always True in numpy.

## Invariants Touched

- **Invariant 2 (Deterministic workflows): preserves** — pure rasterio + numpy, no LLM calls.
- **Invariant 7 (Claims carry provenance): preserves** — result dict carries `value_raster`, `zone_input`, `computed_at`.
- **Invariant 8 (Cancellation first-class): preserves** — uses synchronous `read_through`; asyncio cancel chain applies.
- **FR-DC-6 (cacheable): honors** — `cacheable=True`, `ttl_class="dynamic-1h"`, `source_class="zonal_statistics"`.

## Open Questions

- **OQ-83-RASTERSTATS**: Add `rasterstats` to `pyproject.toml`? TENTATIVE: defer; rolling own is sufficient.
- **OQ-83-VECTOR-CRS**: Vector polygons in a different CRS than the value raster will silently produce empty masks (`rasterio.features.rasterize` does not reproject). Needs follow-up if real GeoJSON admin boundaries in EPSG:4326 are rasterized against projected hazard rasters.
- **OQ-83-UNITS**: `units` field returned `None` for the flood COG (no tag set). `publish_layer` could tag rasters at write time.

## Dependencies and Impacts

- Depends on: job-0031 (cache bucket), job-0032 (`register_tool`), `cache.py` (`read_through`)
- Affects: future hazard-exposure workflow jobs (flood exposure, population in zone, etc.)

## Verification

**Tests:** `11 passed, 0 failed` in 0.04s
```
.venv-agent/bin/python -m pytest services/agent/tests/test_compute_zonal_statistics.py -v
```

**Startup:** `compute_zonal_statistics` visible at position 6 of 21 in `--startup-only` output.

**Live E2E:**
```
compute_zonal_statistics(
  value_raster_uri='gs://grace-2-hazard-prod-runs/01KTJX71NKGDMXB9TN0DV75JWK/flood_depth_peak.tif',
  zone_input_uri=<synthetic 135x132 EPSG:32617 center-quarter mask>,
  statistics=['count','sum','mean','max','percentile_95'],
  _bucket='grace-2-hazard-prod-cache',
)
→ count=48,155 | mean=0.3705m | max=1.9447m | p95=0.8914m | computed_at=2026-06-08T08:08:40Z
```
Raster reprojection path triggered automatically (zone 135×132 ≠ value 540×527). Result written to prod cache bucket. **PASS**.
