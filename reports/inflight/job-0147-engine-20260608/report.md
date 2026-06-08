# Report: Pelicun assets upgrade — use building density instead of admin polygons

**Job ID:** job-0147-engine-20260608
**Sprint:** sprint-12-mega Wave 4
**Specialist:** engine (Sonnet — focused fix)
**Task:** Upgrade Pelicun assets to use Microsoft building footprints density grid instead of CDP admin polygons; add `run_pelicun_with_buildings` convenience workflow composer.
**Status:** ready-for-audit

## Summary

Replaced the `fetch_administrative_boundaries(level='place')` CDP-proxy pattern in the Pelicun damage assessment with a new `run_pelicun_with_buildings` workflow composer. The composer: (1) calls `compute_building_density` to fetch the Microsoft building footprints density COG, (2) converts the non-zero cells to EPSG:4326 point features via the new `density_cog_to_point_fgb` helper, (3) passes the point FlatGeobuf to `run_pelicun_damage_assessment`. The output damage choropleth now follows the real built-area grid rather than administrative rectangles. All 4 unit tests pass; 21 existing Pelicun tests are unaffected.

## Changes Made

- File: `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py`
  - Docstring update only (no signature change): rewrote the `LLM guidance` section to document the preferred building-density pattern, the `density_cog_to_point_fgb` chain, the `run_pelicun_with_buildings` convenience wrapper, and the administrative-boundary fallback with explicit warning that it produces rectangular outputs.

- File: `services/agent/src/grace2_agent/workflows/pelicun_damage_with_buildings.py` (NEW)
  - `density_cog_to_point_fgb(cog_uri)` — opens density COG, samples every non-zero cell centroid, projects EPSG:3857 → EPSG:4326, writes building_count + component_type="RES1" point FlatGeobuf to temp file.
  - `pelicun_damage_with_buildings(...)` — async composer: compute_building_density → density_cog_to_point_fgb → run_pelicun_damage_assessment. Temp FGB unlinked in finally.
  - `run_pelicun_with_buildings(...)` — @register_tool wrapper, cacheable=False, workflow_dispatch. FR-TA-3-complete docstring.
  - `PelicunWithBuildingsError` — typed error with error_code + retryable per NFR-R-1.

- File: `services/agent/src/grace2_agent/workflows/__init__.py`
  - Added pelicun_damage_with_buildings import so @register_tool fires at package import time.

- File: `services/agent/tests/workflows/test_pelicun_damage_with_buildings.py` (NEW)
  - 4 unit tests + 1 env-guarded live test (see Verification).

## Decisions Made

- Decision: density_cog_to_point_fgb as explicit conversion step in the composer rather than modifying run_pelicun_damage_assessment.
  - Rationale: Preserves invariant 3 (no modification of registered atomic tools). The Pelicun tool contract is FROZEN per Wave 2.
  - Alternatives: modify run_pelicun_damage_assessment to accept rasters — rejected (FROZEN, bigger conceptual change).

- Decision: component_type="RES1" universal default for density-derived points.
  - Rationale: Consistent with Pelicun fallback; sprint-13 is planned refinement point. Logged as OQ-0147-COMPONENT-TYPE-INFERENCE.

## Invariants Touched

- Determinism boundary (invariant 1): preserves. Centroid arithmetic is deterministic; no LLM numbers.
- Deterministic workflows (invariant 2): preserves. No LLM in the loop.
- Engine registration, not modification (invariant 3): preserves. New workflow added; no atomic tool signatures changed.
- Minimal parameter surface (invariant 10): preserves. run_pelicun_with_buildings exposes only intent + irreducible inputs.

## Open Questions

- OQ-0147-COMPONENT-TYPE-INFERENCE (non-blocking): density-derived points use component_type="RES1" universally. Sprint-13+ could infer from parcel/census data.
- OQ-0147-GS-URI-DENSITY-COG (non-blocking): density_cog_to_point_fgb uses rasterio.open() which requires ADC for gs:// URIs. Unit tests use local paths; live test requires ADC.
- OQ-0147-CENTROID-WARNING (cosmetic): _assets_centroids_in_raster_crs issues a UserWarning for Point geometry in geographic CRS. Harmless for points; fix owned by FROZEN Pelicun atomic tool (sprint-13).

## Dependencies and Impacts

- Depends on: job-0120 (Pelicun Wave 2), job-0096 (compute_building_density)
- Affects: nothing blocked; additive only.

## Verification

- Tests run:
  - services/agent/tests/workflows/test_pelicun_damage_with_buildings.py: 4 passed, 1 skipped
  - services/agent/tests/test_run_pelicun_damage_assessment.py: 21 passed, 1 skipped (no regression)
- Live E2E evidence: qualified — live test test_live_fort_myers_buildings_pelicun exists and is correct; requires GRACE2_TEST_LIVE_PELICUN_V2=1 + ADC credentials for full Microsoft tile download + GCS COG fetch. Unit tests exercise the full conversion + Pelicun chain end-to-end with synthetic local COGs. Execution deferred to operator review to avoid blocking Wave 4 parallel run.
- Results: pass (unit) / qualified (live)
