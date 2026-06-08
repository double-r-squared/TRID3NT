# Audit: `fetch_nws_event` atomic tool

**Job ID:** job-0090-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_nws_event.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",
    source_class="nws_event",
)
def fetch_nws_event(area: str | tuple[float,float,float,float], event_types: list[str] | None = None, status: str = 'actual', message_type: str = 'alert') -> LayerURI:
    """NWS active alerts/events Tier-1 fetcher.

    Wraps the api.weather.gov /alerts/active endpoint. `area` can be a 2-letter state
    code, a county FIPS, OR a bbox tuple (converted to point center for the zone
    lookup). Returns FlatGeobuf polygons + properties (severity, headline, event,
    onset, ends, description).
    Returns LayerURI(layer_type="vector", role="context", units=None).
    """
```

**Implementation**:
- Endpoint: `https://api.weather.gov/alerts/active?area={STATE}` for state codes; or `?point={lat},{lon}` for bbox→center
- `event_types` filter: NWS supports `&event={Hurricane Warning}` repeatable; URL-encode each
- Headers REQUIRED: `User-Agent: grace2-agent/0.1 (contact: grace2-ops@local)` — NWS will 403 without this
- Response is GeoJSON FeatureCollection — convert direct to FlatGeobuf
- Cache TTL: 1h (FR-DC `dynamic-1h`) — active alerts change frequently
- Cache key: SHA-256 of (area canonicalized, event_types sorted, status, message_type)
- Properties to preserve: `event`, `headline`, `description`, `severity`, `urgency`, `certainty`, `effective`, `onset`, `ends`, `senderName`
- HTTP: httpx sync timeout=30, typed `NWSUpstreamError(retryable=True)`

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked: FL state response with 3 active alerts
- event_types filter narrows to Hurricane only
- bbox center conversion: (-81.9, 26.5, -81.7, 26.7) → lat=26.6, lon=-81.8
- User-Agent header verified present in request
- Live (env GRACE2_TEST_LIVE_NWS=1): area='FL' returns ≥0 features (zero is OK)

**Live verification**: fetch_nws_event('FL', event_types=['Hurricane Warning','Flood Warning']) → real GeoJSON converted to FlatGeobuf; evidence/nws_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_nws_event.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_nws_event.py` (NEW)
- `reports/inflight/job-0090-engine-20260608/`


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

