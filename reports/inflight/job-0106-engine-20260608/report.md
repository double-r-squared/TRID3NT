# Report: `clip_raster_to_polygon` utility tool

**Job ID:** job-0106-engine-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** engine
**Task:** New atomic tool `clip_raster_to_polygon(raster_uri, polygon_uri, feature_filter?, nodata_outside?) -> LayerURI`
**Status:** ready-for-audit

## Summary

Added new atomic tool `clip_raster_to_polygon` that clips a raster to an arbitrary polygon
(vs sibling `clip_raster_to_bbox` which only does rectangles). It is the substrate for the
"in [named place]" geographic-clipping pattern — composes with `fetch_administrative_boundaries`,
`fetch_wdpa_protected_areas`, or any polygon-emitting fetcher to mask rasters to a region's
exact outline before `compute_zonal_statistics` or display. Implementation uses `rasterio.mask.mask`
with `crop=True`, reprojects polygon to raster CRS via `geopandas.to_crs`, supports
`feature_filter={"property":..., "value":...}` for multi-feature inputs, and routes through the
FR-DC-3 cache shim (`static-30d`, `source_class="clip_raster_polygon"`).

## Changes Made

- **NEW** `services/agent/src/grace2_agent/tools/clip_raster_to_polygon.py` (~420 lines)
  - `clip_raster_to_polygon` decorated with `@register_tool(AtomicToolMetadata(...))`
    (`cacheable=True, ttl_class="static-30d", source_class="clip_raster_polygon"`).
  - `ClipRasterPolygonError` typed error with 9 error_codes (UNKNOWN_RASTER_URI,
    RASTER_OPEN_FAILED, RASTER_DOWNLOAD_FAILED, UNKNOWN_POLYGON_URI, POLYGON_OPEN_FAILED,
    POLYGON_DOWNLOAD_FAILED, POLYGON_FILTER_EMPTY, POLYGON_REPROJECT_FAILED, MASK_FAILED).
  - FR-TA-3-complete docstring: Use this when / Do NOT use this for / Params / Returns /
    LLM guidance / Raises.
  - Raster reading via rasterio (gs:// -> /vsigs/ for header-only CRS detection); GCS bytes
    via google.cloud.storage (lazy import).
  - Polygon reading via geopandas/pyogrio (FlatGeobuf, GeoJSON, Shapefile, GeoPackage).
  - CRS reprojection via `gdf.to_crs(target_crs)` when polygon CRS != raster CRS.
  - `feature_filter` applies attribute-equality filter before mask; empty result raises
    POLYGON_FILTER_EMPTY (retryable=False).
  - `nodata_outside` override; falls back to source nodata, then dtype-appropriate default
    (NaN for float, 0 for integer).
  - LZW-compressed GeoTIFF output via `rasterio` with `crop=True` so output extent shrinks
    to polygon bbox.
  - Cache key on `(raster_uri, polygon_uri, feature_filter, nodata_outside)` via existing
    `read_through` shim.
- `services/agent/src/grace2_agent/tools/__init__.py` -- 1-line eager import for
  `clip_raster_to_polygon` (preserves FR-CE-8 fail-fast).
- `services/agent/src/grace2_agent/main.py` -- 1-line eager import in `_import_tools_registry`.
- **NEW** `services/agent/tests/test_clip_raster_to_polygon.py` (~430 lines, 10 tests):
  registration + 6 unit cases + 2 typed-error cases + 1 live geographic-correctness gate.

## Decisions Made

- **Decision:** Use `rasterio.mask.mask` (with `crop=True`) rather than `qgis_process` or
  GDAL CLI. **Rationale:** rasterio.mask is a pure-Python primitive that ships with the
  agent env (no GDAL binary dependency), handles arbitrary polygon geometry natively,
  matches the kickoff's spec exactly. **Alternative considered:** Calling `gdalwarp -cutline`
  in a subprocess like the sibling `clip_raster_to_bbox`; rejected because rasterio.mask
  is more idiomatic for arbitrary polygon clipping and avoids the PROJ_LIB conda-env
  workaround the sibling needs.
- **Decision:** Reproject polygon to raster's native CRS (not the other way around).
  **Rationale:** Reprojecting raster pixels is lossy (resampling); reprojecting polygon
  vertices is exact. Sibling pattern across the codebase.
- **Decision:** `feature_filter` schema is `{"property": str, "value": Any}` for attribute
  equality. **Rationale:** Matches the kickoff exactly. Caller composes with regex/range
  filters via `qgis_process` first if needed.
- **Decision:** When source nodata is undefined and float dtype, use NaN; when integer, use 0.
  **Rationale:** rasterio.mask requires a nodata fill value for `crop=True`; these are the
  conventional defaults. Caller can always override with `nodata_outside`.
- **Decision:** Output extent shrinks to polygon bbox (`crop=True`). **Rationale:** Matches
  the kickoff's stated estimate_payload_mb logic (smaller payload = polygon area / source area).
- **Decision:** `AtomicToolMetadata` fields use existing contract (name, ttl_class,
  source_class, cacheable). The kickoff's decorator example listed `supports_global_query=False`
  but that field does not exist on the canonical `AtomicToolMetadata` model; I followed the
  actual contract pattern shared by all 23 sibling tools. **Flagged:** OQ-0106-METADATA-FIELDS.

## Invariants Touched

- **2. Deterministic workflows:** preserves -- zero LLM calls; pure Python rasterio.mask.
- **3. Engine registration, not modification:** preserves -- added new tool via
  `@register_tool`; no changes to agent core or registry mechanism.
- **CRS hygiene end-to-end:** extends -- polygon reprojected to raster CRS before mask;
  output preserves raster's native CRS; geographic-correctness gate verifies pixel center
  coords fall inside requested polygon.
- **NFR-R-1 (resilience):** preserves -- every failure mode (bad URI, missing file, bad
  filter, reproject failure, mask failure) surfaces as typed `ClipRasterPolygonError` with
  SCREAMING_SNAKE_CASE error_code, never an uncaught exception.
- **FR-DC-6 cacheability:** honors -- `cacheable=True, ttl_class="static-30d"`,
  `source_class="clip_raster_polygon"`; routed through `read_through`.

## Open Questions

- **OQ-0106-METADATA-FIELDS** (non-blocking, low priority) -- The kickoff decorator example
  showed `supports_global_query=False` but this field is NOT on `AtomicToolMetadata`
  (canonical fields: name, ttl_class, source_class, cacheable). I followed the actual
  contract. If a future contract revision adds discovery hints, this tool's registration
  can be updated additively.
- **OQ-0106-LAYER-ID-STYLE** (non-blocking, low priority) -- `layer_id` format is
  `clip-poly-<raster>-<polygon>[-<filter_value>]` -- chose for diagnostic readability over
  hash purity. Matches sibling `clip_raster_to_bbox` pattern.
- **OQ-0106-ALL-TOUCHED** (non-blocking, low priority) -- `all_touched=False` (default)
  excludes pixels whose centers are outside the polygon. For very small polygons or coarse
  rasters, `all_touched=True` might preserve more pixels. Surfaced for the orchestrator;
  current default matches conservative scientific convention.

## Dependencies and Impacts

- **Depends on:** job-0085 (clip_raster_to_bbox; sibling pattern for cache+gs:// download),
  job-0084 (fetch_administrative_boundaries; polygon source for live test composition).
- **Affects:** workflows can now compose `fetch_administrative_boundaries -> clip_raster_to_polygon`
  for "in [state]" pattern. Future engine work (M5.5 Pelicun, conservation tooling)
  benefits from in-protected-area or in-county masking.

## Verification

### Unit tests (9 passed, 1 live-skipped -> all 10 passed with env var)

```
$ .venv-agent/bin/python -m pytest tests/test_clip_raster_to_polygon.py -v
tests/test_clip_raster_to_polygon.py::test_clip_raster_to_polygon_registered PASSED
tests/test_clip_raster_to_polygon.py::test_clip_with_square_polygon_yields_correct_extent PASSED
tests/test_clip_raster_to_polygon.py::test_polygon_crs_mismatch_is_reprojected PASSED
tests/test_clip_raster_to_polygon.py::test_feature_filter_selects_one_polygon PASSED
tests/test_clip_raster_to_polygon.py::test_nodata_outside_override PASSED
tests/test_clip_raster_to_polygon.py::test_cache_miss_then_hit_skips_mask PASSED
tests/test_clip_raster_to_polygon.py::test_empty_filter_raises_typed_error PASSED
tests/test_clip_raster_to_polygon.py::test_unknown_raster_uri_raises_typed_error PASSED
tests/test_clip_raster_to_polygon.py::test_unknown_polygon_uri_raises_typed_error PASSED
tests/test_clip_raster_to_polygon.py::test_live_clip_fortmyers_dem_to_lee_county_shape SKIPPED

========================= 9 passed, 1 skipped in 0.30s =========================
```

### Live geographic-correctness gate (per codified lesson #1)

```
$ GRACE2_TEST_LIVE_CLIP=1 pytest tests/test_clip_raster_to_polygon.py::test_live_clip_fortmyers_dem_to_lee_county_shape -v -s
LIVE CLIP RESULT:
  bounds=BoundingBox(left=-82.200, bottom=26.449, right=-81.600, top=26.820)
  valid_pixels=129132/173020 (74.6%)
  sample_inside=10/10
PASSED
```

The geographic-correctness check verified that 10/10 sampled valid pixels fell **inside**
the Lee-County-shaped polygon (not just inside its bounding box), AND that 25.4% of the
output extent was masked to nodata (the polygon is not the bbox -- masking is real, not
just a rectangle clip).

### Live invocation (Washington state example)

```
=== Tool registered: True
=== Tool count: 25
=== LayerURI:
    layer_id: clip-poly-dem-wash-Washington
    name: Clipped raster (polygon mask)-Washington
    uri: gs://test-bucket/cache/static-30d/clip_raster_polygon/36b11c25ce2f7b7676d964249525260b.tif
    layer_type: raster
    style_preset: continuous_dem
=== Clipped output:
    bounds: BoundingBox(left=-124.701, bottom=45.5, right=-116.998, top=49.0)
    crs: EPSG:4326
    shape: 256 x 232
    valid pixels: 59392/59392 (100.0%)
    valid mean: 499.74, min: 0.01, max: 999.97
=== Geographic correctness: bounds inside Washington bbox confirmed
=== Cache hit on second call: same URI = True, elapsed = 0.4ms
```

Composed multi-feature FlatGeobuf (Washington + Oregon + Idaho) with
`feature_filter={"property":"NAME","value":"Washington"}` -> output bounds match Washington's
bbox EXACTLY (-124.7, 45.5, -117.0, 49.0); Oregon and Idaho are excluded from output.

### Sibling regression check (no breakage)

```
$ pytest tests/test_clip_raster_to_polygon.py tests/test_clip_raster_to_bbox.py \
         tests/test_fetch_administrative_boundaries.py tests/test_tools_registry.py \
         tests/test_tools_cache.py tests/test_main_startup.py -q
61 passed, 4 skipped, 4 warnings in 1.53s
```

### Registry contents (post-registration)

```
tool count after job-0106 registration: 25 (was 24)
clip_raster_to_polygon in TOOL_REGISTRY: True
metadata.cacheable: True
metadata.ttl_class: 'static-30d'
metadata.source_class: 'clip_raster_polygon'
```

- **Results:** pass -- 9/9 unit tests + 1/1 live geographic-correctness test + ad-hoc live
  invocation all green; no sibling regression. Geographic-correctness gate (codified lesson #1)
  satisfied: 10/10 sampled valid pixels fall inside the requested polygon, not just its bbox.
