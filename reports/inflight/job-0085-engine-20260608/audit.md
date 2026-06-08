# Audit: `clip_raster_to_bbox` atomic tool

**Job ID:** job-0085-engine-20260608, **Sprint:** sprint-11 Stage 1 parallel, **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Prerequisites:** mirror compute_hillshade / compute_slope / compute_aspect (subprocess-wrap-gdal pattern). Cache integration per FR-DC.

**Required reads:**
- `services/agent/src/grace2_agent/tools/compute_slope.py` (after 0081 landed) — pattern reference for subprocess + cache
- `services/agent/src/grace2_agent/tools/cache.py`

### Scope

NEW file `services/agent/src/grace2_agent/tools/clip_raster_to_bbox.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="clip_raster",
)
def clip_raster_to_bbox(
    raster_uri: str,
    bbox: tuple[float, float, float, float],
    bbox_crs: str = "EPSG:4326",
    target_crs: str | None = None,
) -> LayerURI:
    """Clip a raster to a bounding box, optionally reprojecting.

    Wraps `gdal_translate -projwin` (if bbox_crs matches raster CRS) OR
    `gdalwarp -te -te_srs` (if reprojection needed). Returns a new
    LayerURI pointing at the clipped raster in cache.

    Parameters:
        raster_uri: source raster (gs:// or local).
        bbox: (west, south, east, north) extent.
        bbox_crs: CRS that bbox is expressed in (default WGS84).
        target_crs: if provided, reproject output to this CRS;
                    else preserve source raster CRS.

    LLM guidance:
        - Use this when a fetched raster is larger than the case bbox
          (e.g., a national-scale DEM clipped to a state case).
        - bbox_crs default "EPSG:4326" matches user-friendly lat/lon
          inputs; agent should usually pass user-facing bbox here.
    """
```

**Implementation**:
- Cache key on (raster_uri, bbox_rounded_6dp, bbox_crs, target_crs).
- Download source via `/vsigs/` or local path detection.
- If `target_crs` is None AND `bbox_crs` matches source raster CRS: `gdal_translate -projwin <bbox> <input> <output>` (fast).
- Else: `gdalwarp -te <bbox> -te_srs <bbox_crs> [-t_srs <target_crs>] <input> <output>` (reprojection-capable).
- Output: GeoTIFF at `cache/static-30d/clip_raster/<hash>.tif`.
- Read source CRS via `rasterio.open(...).crs` to decide the gdal_translate-vs-gdalwarp path.

**Tests** (≥4):
- Synthetic 256×256 raster + bbox covering top-right quadrant → returns ~128×128 clip
- Same raster + reprojection (input EPSG:4326, output EPSG:3857) → output in correct CRS
- Cache miss writes; hit skips gdal
- Unknown raster_uri raises typed error

**Live verification**: clip job-0075's Fort Myers DEM by half the bbox → smaller GeoTIFF in cache.

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/clip_raster_to_bbox.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_clip_raster_to_bbox.py` (NEW)
- `reports/inflight/job-0085-engine-20260608/`

### FROZEN

All other tools/*; all workflows/, services/workers/, packages/contracts/, web/, infra/, docs/srs/, styles/. `reports/complete/**`.

### Concurrency note

3 concurrent (0078 web + 0079 hillshade + 0084 admin). Idempotent registration; append if conflict.

### Acceptance

- [ ] Registered + visible at `--startup-only`
- [ ] gdal_translate path + gdalwarp reproject path both work
- [ ] Tests pass
- [ ] Live verification on Fort Myers DEM
- [ ] No FROZEN edits
- [ ] Single commit
