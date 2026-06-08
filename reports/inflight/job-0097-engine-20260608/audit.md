# Audit: `fetch_roads_osm` atomic tool

**Job ID:** job-0097-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_roads_osm.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="osm_roads",
)
def fetch_roads_osm(bbox: tuple[float,float,float,float], road_classes: list[str] | None = None) -> LayerURI:
    """OpenStreetMap roads fetcher via Overpass API.

    Queries the OSM Overpass API for road features in the bbox, returns FlatGeobuf
    linestring features. road_classes filter (highway tag values, e.g.
    ['motorway','trunk','primary','secondary']); default is the major + arterial set.
    Returns LayerURI(layer_type="vector", role="context", units=None).
    """
```

**Implementation**:
- Endpoint: `https://overpass-api.de/api/interpreter` (POST data=`<Overpass QL>`)
- Overpass QL template: `[out:json][timeout:60];(way["highway"~"^({pipe-joined classes})$"]({s},{w},{n},{e});); out geom;`
- Default road_classes: ['motorway','trunk','primary','secondary','tertiary','motorway_link','trunk_link','primary_link']
- Response: JSON with way objects → convert to LineString geometries + properties (name, highway, lanes, maxspeed)
- Cache: static-30d (roads change slowly)
- Cache key on (bbox-rounded-6dp, road_classes sorted tuple)
- Cache prefix: cache/static-30d/osm_roads/<hash>.fgb
- HTTP: httpx sync timeout=120 (Overpass can be slow); typed `OSMUpstreamError(retryable=True)`
- Polite: between calls insert 1s sleep to respect Overpass rate limits

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked: 50-way response → 50 features in FlatGeobuf
- road_classes filter narrows to motorway only
- Empty bbox → 0 features, no error
- Overpass 504 → typed retryable error
- Cache miss/hit
- Live (env GRACE2_TEST_LIVE_OSM=1): Fort Myers small bbox → ≥1 feature with name property

**Live verification**: fetch_roads_osm((-82.0, 26.5, -81.8, 26.7), road_classes=['primary','motorway']) → real FlatGeobuf with I-75 + US-41; evidence/osm_roads_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_roads_osm.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_roads_osm.py` (NEW)
- `reports/inflight/job-0097-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1 sibling has its own file ownership); all `workflows/`, `services/workers/`, `packages/contracts/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`.


### Concurrency note (Wave 1 fan-out)

~15 Wave 1 jobs run concurrently. Each owns its own NEW tool file but ALL share `tools/__init__.py` + `main.py` registration sites. The idempotent-append pattern from sprint-11 Stage 1 (which handled 6 concurrent additions cleanly) applies: ADD your import line at the end of each file; if your line conflicts with a sibling's, do `git pull --rebase` style re-apply; do NOT remove other tool imports.


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".


### Acceptance criteria

- [ ] New tool registered + visible at `--startup-only` (count = entering_count + 1)
- [ ] ≥4 unit tests + 1 live test (with appropriate env-var guard)
- [ ] Live verification with real upstream response captured to evidence/
- [ ] Geography correctness check per the codified job-0086 lesson (where applicable)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence paths + any OQs

