# Audit: `clip_raster_to_polygon` utility tool

**Job ID:** job-0106-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/clip_raster_to_bbox.py` (job-0085 — sibling pattern)
- `services/agent/src/grace2_agent/tools/cache.py`

### Scope

NEW file `services/agent/src/grace2_agent/tools/clip_raster_to_polygon.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="clip_raster_polygon",
    supports_global_query=False,
)
def clip_raster_to_polygon(
    raster_uri: str,
    polygon_uri: str,
    feature_filter: dict | None = None,
    nodata_outside: float | None = None,
) -> LayerURI:
    """Clip a raster to an arbitrary polygon (vs job-0085 which only does bbox rectangles).

    Enabler for the "in [place]" geographic-clipping pattern (per
    feedback-geographic-clipping-pattern memory rule). Composes with:
        fetch_administrative_boundaries(level='state') → polygon_uri
        clip_raster_to_polygon(precip_uri, polygon_uri) → masked_uri

    feature_filter: optional dict {"property": "name", "value": "Washington"} —
        if the vector has multiple polygons, select only matching ones BEFORE clip
    nodata_outside: value to assign to pixels outside the polygon (default: source nodata)

    Implementation: rasterio.mask.mask with the polygon geometry; reproject polygon
    to raster CRS first if mismatched.
    """
```

**Implementation**:
- Read raster_uri via rasterio (handle /vsigs/ for gs:// paths)
- Read polygon_uri via fiona OR geopandas; apply feature_filter if given
- Reproject polygon to raster CRS via rasterio.warp.transform_geom
- `rasterio.mask.mask(raster, [polygon_geom], crop=True, nodata=nodata_outside)`
- Write output GeoTIFF (LZW compressed)
- Cache key: SHA-256 of (raster_uri, polygon_uri, feature_filter, nodata_outside)
- Cache prefix: cache/static-30d/clip_raster_polygon/<hash>.tif
- estimate_payload_mb: equal to clipped area / source area × source size (estimate from source raster's dimensions)

**Tests** (≥5 unit + 1 live):
- Synthetic raster + simple square polygon → clipped raster with correct extent
- Polygon vs source CRS mismatch → reprojected polygon used
- feature_filter selects one polygon out of multi-feature vector
- nodata_outside override applies
- Cache miss/hit
- Live (env GRACE2_TEST_LIVE_CLIP=1): clip Fort Myers DEM to TIGER Lee County polygon → verify clipped result

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/clip_raster_to_polygon.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_clip_raster_to_polygon.py` (NEW)
- `reports/inflight/job-0106-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1.5 sibling owns one); all `workflows/`, `services/workers/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`. For schema/agent jobs, FROZEN is the inverse of their declared file ownership.

### Concurrency note (Wave 1.5 fan-out — 16 parallel)

~16 Wave 1.5 jobs in parallel. Idempotent-append works for `tools/__init__.py` + `main.py` + `packages/contracts/__init__.py` but Wave 1 produced 3 commit-label-swap patterns under load. **Required mitigation**: before `git commit`, run `git pull --rebase=true origin main 2>/dev/null || git stash && git pull --rebase && git stash pop` to handle sibling concurrent landings cleanly. If conflict on registration site, re-apply your import line.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: if your tool emits geometry, verify against actual geography (river mouth where it should be, not just bbox/URL consistency). Every fetcher's live test must check that emitted features fall inside requested bbox AND match the named place's actual outline if applicable.

2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.

### Acceptance criteria

- [ ] New tool/contract registered + visible at appropriate test surface
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness check where applicable
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

