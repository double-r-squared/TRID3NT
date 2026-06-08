# Audit: `fetch_administrative_boundaries` atomic tool

**Job ID:** job-0084-engine-20260608, **Sprint:** sprint-11 Stage 1 parallel, **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Prerequisites:** fetch_river_geometry (job-0039) — mirror that pattern exactly (Tier-1 fetcher, FlatGeobuf return, cache static-30d, bbox-clip).

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_river_geometry.py` — pattern reference
- `services/agent/src/grace2_agent/tools/cache.py` — read_through

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="admin_boundaries",
)
def fetch_administrative_boundaries(
    level: Literal["state", "county", "place", "zcta"],
    bbox: tuple[float, float, float, float],
) -> LayerURI:
    """Fetch US Census TIGER/Line administrative-boundary polygons.

    Tier-1 free (no API key); annual TIGER/Line releases hosted at
    https://www2.census.gov/geo/tiger/TIGER<year>/. Returns FlatGeobuf
    with the polygons clipped to the requested bbox.

    level:
        "state": 50 US states + DC + territories
        "county": 3000+ US counties
        "place": cities + towns + CDPs (Census Designated Places)
        "zcta": ZIP Code Tabulation Areas

    Returns LayerURI(layer_type="vector", role="context", units=None).
    """
```

**Implementation**:
- TIGER/Line URLs: `https://www2.census.gov/geo/tiger/TIGER2024/<LEVEL>/tl_2024_us_<level>.zip` (or per-state for counties). Pin year to 2024 (most recent stable).
- Strategy A (state/county nationwide files): download the full shapefile ZIP → unzip → clip to bbox via fiona/geopandas → write FlatGeobuf to cache. Cache key on (level, bbox-rounded).
- Strategy B (per-state county/place files): use FIPS lookup → download only the state(s) the bbox touches → clip. More bandwidth-efficient.
- Pick A for simplicity; B is a sprint-12 optimization if performance matters.
- Cache key: SHA256 of (level, bbox-rounded-to-6dp, year).
- Output: FlatGeobuf at `cache/static-30d/admin_boundaries/<hash>.fgb`.

**Tests** (≥4):
- Bbox covering one state → returns polygons with at least 1 feature
- Bbox over Fort Myers FL → returns Lee County (county level) / "Fort Myers" CDP (place level)
- Cache miss writes; hit skips download
- Unknown level raises typed error

**Live verification**: `fetch_administrative_boundaries(level="county", bbox=fort_myers_bbox)` → real FlatGeobuf containing Lee County polygon.

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_fetch_administrative_boundaries.py` (NEW)
- `reports/inflight/job-0084-engine-20260608/`

### FROZEN

All other tools/*; all workflows/, services/workers/, packages/contracts/, web/, infra/, docs/srs/, styles/. `reports/complete/**`.

### Concurrency note

3 concurrent jobs (0078 web + 0079 hillshade + 0085 clip) each touch main.py + tools/__init__.py. Idempotent additions; if conflict, append.

### Acceptance

- [ ] Registered + visible at `--startup-only`
- [ ] 4 levels work
- [ ] Tests pass
- [ ] Live verification on Fort Myers
- [ ] No FROZEN edits
- [ ] Single commit
