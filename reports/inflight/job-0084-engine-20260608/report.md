# Report: `fetch_administrative_boundaries` atomic tool

**Job ID:** job-0084-engine-20260608
**Sprint:** sprint-11 Stage 1 parallel
**Specialist:** engine
**Task:** NEW `tools/fetch_administrative_boundaries.py` — TIGER/Line 2024 polygons (state/county/place/zcta), Strategy A, FR-DC cache static-30d, source_class="admin_boundaries", returns LayerURI(layer_type="vector", role="context").
**Status:** ready-for-audit

## Summary

Implemented `fetch_administrative_boundaries` as a new atomic tool following the existing fetcher pattern. Downloads US Census TIGER/Line 2024 shapefiles from `https://www2.census.gov/geo/tiger/TIGER2024/`, unzips to a temp dir, clips to bbox using geopandas/pyogrio, serializes as FlatGeobuf through the FR-DC-3 read_through cache shim. All four levels implemented. Live E2E verified: Fort Myers FL bbox → real FlatGeobuf with Lee County polygon (3 features, 54,824 bytes, 19.78s real download).

## Changes Made

- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (NEW): full implementation with typed error hierarchy, FIPS bbox table, URL builders, download+clip+FlatGeobuf pipeline, registered atomic tool.
- `services/agent/src/grace2_agent/tools/__init__.py`: 1 import line appended after job-0085 line (idempotent per concurrency note).
- `services/agent/src/grace2_agent/main.py`: 1 import line appended after job-0085 line in `_import_tools_registry()`.
- `services/agent/tests/test_fetch_administrative_boundaries.py` (NEW): 19 unconditional + 3 live tests.

## Decisions Made

- **geopandas intersects() not clip():** Returns whole polygons intersecting bbox rather than geometry-clipped shapes. Admin boundaries as context overlays should be whole features.
- **Place is per-state, others nationwide:** No nationwide place ZIP exists in TIGER 2024. Per-state files (~5-10 MB each) are used; multi-state bbox queries download and merge.
- **CRS reprojection:** TIGER 2024 ships in EPSG:4269 (NAD83). Reprojected to EPSG:4326 before bbox clip.
- **geopandas/pyogrio not added to pyproject.toml:** Available as transitive deps (hydromt→geopandas→pyogrio). Explicit pin is a schema/infra concern; surfaced as OQ.

## Invariants Touched

- Engine registration, not modification: extends — new file, no existing tool modified.
- Tier separation: preserves — Tier-1 free, no API key.
- Metadata-payload pattern: preserves — read_through with quantized params dict.
- Cancellation is first-class: preserves — sync I/O compatible with cancel chain.

## Open Questions

- OQ-84-ZCTA-DOWNLOAD-SIZE: ZCTA nationwide ZIP is 504 MB; first-fetch latency 30-120s. TENTATIVE: accept for v0.1 (30-day cache). Sprint-12 optimization candidate.
- OQ-84-GEOPANDAS-PYOGRIO-EXPLICIT-DEPS: geopandas and pyogrio are transitive but not explicitly pinned in pyproject.toml. Routing: schema/infra.
- OQ-84-TIGER-YEAR-ADVANCEMENT: Year pinned to "2024". Future: `year` param or auto-detect. Cache key includes year so it's backward-compatible.

## Dependencies and Impacts

- Depends on: job-0031 (GCS bucket), read_through cache shim.
- Affects: none (additive only).

## Verification

Non-live tests: 19 passed, 3 skipped in 0.04s.
Live tests (GRACE2_TEST_LIVE_TIGER=1): 3 passed in 30.7s.
Combined suite (test_fetch_administrative_boundaries + test_main_startup + test_tools_registry + test_tools_cache): 44 passed, 3 skipped in 0.99s.

--startup-only: fetch_administrative_boundaries visible in 23-tool registry; exit 0.

Live E2E: _fetch_admin_boundaries_bytes(level='county', bbox=(-82.3, 26.3, -81.6, 26.8)) → 54,824 bytes FlatGeobuf → 3 features: Collier County, Lee County, Charlotte County. Lee County found: True.
