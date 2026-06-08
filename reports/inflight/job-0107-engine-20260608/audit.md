# Audit: `clip_vector_to_polygon` utility tool

**Job ID:** job-0107-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (vector pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py`

### Scope

NEW file `services/agent/src/grace2_agent/tools/clip_vector_to_polygon.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="clip_vector_polygon",
    supports_global_query=False,
)
def clip_vector_to_polygon(
    vector_uri: str,
    polygon_uri: str,
    feature_filter: dict | None = None,
    keep_partial: bool = True,
) -> LayerURI:
    """Clip a vector (points/lines/polygons) to an arbitrary polygon.

    Sibling to clip_raster_to_polygon (job-0106). Enabler for the "in [place]"
    geographic-clipping pattern for vector layers (e.g. clip nationwide NWS
    alerts to a state polygon).

    keep_partial: True = keep features that PARTIALLY intersect (default);
                  False = require full containment (centroid-within for points)
    """
```

**Implementation**:
- Read vector_uri via geopandas/pyogrio
- Read polygon_uri; apply feature_filter; dissolve to single geometry if multi-poly
- Reproject polygon to vector CRS if mismatched
- For points: gpd.GeoDataFrame.sjoin(polygon, predicate='intersects' or 'within' based on keep_partial)
- For lines/polygons: gpd.GeoDataFrame.intersection(polygon) if keep_partial else gpd.GeoDataFrame.within(polygon)
- Write output as FlatGeobuf (same format as inputs)
- Cache key: SHA-256 of (vector_uri, polygon_uri, feature_filter, keep_partial)
- Cache prefix: cache/static-30d/clip_vector_polygon/<hash>.fgb

**Tests** (≥5 unit + 1 live):
- Points within polygon retained; outside discarded
- Polygons partially overlapping with keep_partial=True kept; False=discarded
- Lines crossing polygon boundary: keep_partial behavior
- feature_filter on multi-feature polygon source
- Cache miss/hit
- Live (env GRACE2_TEST_LIVE_CLIPV=1): clip nationwide GBIF panther occurrences to TIGER FL state polygon → fewer features inside FL

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/clip_vector_to_polygon.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_clip_vector_to_polygon.py` (NEW)
- `reports/inflight/job-0107-engine-20260608/`


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

