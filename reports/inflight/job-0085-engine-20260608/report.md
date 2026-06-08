# Report: `clip_raster_to_bbox` atomic tool

**Job ID:** job-0085-engine-20260608
**Sprint:** sprint-11 Stage 1 parallel
**Specialist:** engine
**Status:** ready-for-audit

## Summary

Implemented `clip_raster_to_bbox` as a new atomic tool following the `compute_slope` pattern. Selects `gdal_translate -projwin` (fast path) or `gdalwarp -te -te_srs [-t_srs]` (reprojection path) via rasterio CRS comparison. Key environment fix: `_gdal_subprocess_env()` sets `PROJ_LIB=/usr/share/proj` for subprocesses, resolving a conda-GDAL proj.db schema version mismatch. All 8 unit tests pass; full suite 261 passed, 8 expected skips. Live E2E on Fort Myers DEM: 122x130 → 61x130 (50% width clip).

## Changes Made

- NEW: `services/agent/src/grace2_agent/tools/clip_raster_to_bbox.py`
- APPENDED: `services/agent/src/grace2_agent/tools/__init__.py` (1 line eager import)
- APPENDED: `services/agent/src/grace2_agent/main.py` (1 line in _import_tools_registry)
- NEW: `services/agent/tests/test_clip_raster_to_bbox.py` (8 tests)

## Decisions Made

- Fast path omits `-projwin_srs`: only taken when bbox_crs==source_crs, so coords are already native. Omitting avoids PROJ DB lookup that fails in conda subprocess context (proj.db schema v4, expected >=6).
- `_gdal_subprocess_env()` uses `/usr/share/proj`: gdalwarp needs PROJ for `-te_srs`/`-t_srs`; system PROJ works in dev and Cloud Run containers. `GRACE2_PROJ_LIB` env var for override.

## Invariants Touched

All 10 invariants: preserves (details in full report above).

## Open Questions

- OQ-1 (non-blocking): `/usr/share/proj` hardcoded fallback harmless in production (Cloud Run uses Debian GDAL with system PROJ natively). Accept unless container changes.
- OQ-2 (non-blocking): CRS comparison via rasterio falls back to gdalwarp on representation mismatch — safe but slower. Live test confirmed correct fast-path selection for EPSG:5070.

## Dependencies and Impacts

- Depends on: job-0081 (pattern), job-0075 (Fort Myers DEM)
- Affects: utility primitive for any workflow step needing bbox-clipped raster input

## Verification

Tests: 8/8 pass. Full suite: 261 pass, 8 skip. Startup-only: tool registered (23 total). Live E2E: Fort Myers DEM clip 122x130 → 61x130, URI gs://grace-2-hazard-prod-cache/cache/static-30d/clip_raster/4dcfdcd1b5867380d4dbbcfe6753613f.tif.

- Results: **pass**
