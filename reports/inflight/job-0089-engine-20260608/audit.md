# Audit: `fetch_wdpa_protected_areas` atomic tool

**Job ID:** job-0089-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_wdpa_protected_areas.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="wdpa",
)
def fetch_wdpa_protected_areas(bbox: tuple[float,float,float,float], designation_filter: list[str] | None = None) -> LayerURI:
    """World Database on Protected Areas (UNEP-WCMC) Tier-1 polygon fetcher.

    Uses the WDPA ArcGIS REST FeatureServer endpoint (no auth required for read).
    Returns FlatGeobuf polygons clipped to bbox. designation_filter optional list of
    designation names (e.g. ['National Park','National Wildlife Refuge']).
    Returns LayerURI(layer_type="vector", role="context", units=None).
    """
```

**Implementation**:
- Endpoint: `https://services3.arcgis.com/Mj0hjvkNtV7NRhA7/arcgis/rest/services/WDPA_v0/FeatureServer/0/query?where=1=1&geometry={bbox_envelope}&geometryType=esriGeometryEnvelope&inSR=4326&outFields=NAME,DESIG_ENG,IUCN_CAT,STATUS,STATUS_YR,ISO3,WDPAID&outSR=4326&f=geojson&resultRecordCount=2000`
- Pagination via `resultOffset` if more than 2000 features
- `designation_filter`: client-side filter on `DESIG_ENG` property after fetch (server-side filter is fragile across WDPA mirrors)
- Cache key: SHA-256 of (bbox-rounded-6dp, designation_filter sorted tuple)
- Output: FlatGeobuf polygons, EPSG:4326, properties from outFields
- Cache prefix: `cache/static-30d/wdpa/<hash>.fgb`
- HTTP: httpx sync timeout=60 (WDPA can be slow); typed `WDPAUpstreamError(retryable=True)`

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked: 100-feature response → 100 polygons in FlatGeobuf
- designation_filter='National Park' returns subset
- Empty bbox over open water → 0 features without error
- Pagination across 4000 features
- Live (env GRACE2_TEST_LIVE_WDPA=1): Everglades bbox returns Everglades National Park polygon

**Live verification**: fetch_wdpa_protected_areas((-81.5, 25.0, -80.5, 26.5)) → real FlatGeobuf containing Everglades NP; evidence/wdpa_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_wdpa_protected_areas.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_wdpa_protected_areas.py` (NEW)
- `reports/inflight/job-0089-engine-20260608/`


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

