# Audit: `fetch_storm_events_db` atomic tool

**Job ID:** job-0091-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_storm_events_db.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="storm_events",
)
def fetch_storm_events_db(year: int, state: str | None = None, event_types: list[str] | None = None) -> LayerURI:
    """NOAA Storm Events Database Tier-1 fetcher.

    Downloads the annual storm events CSV from https://www.ncei.noaa.gov/data/storm-events/
    Returns FlatGeobuf with point geometry (begin_lat/begin_lon). state ISO 2-letter
    filter; event_types client-side filter on EVENT_TYPE column.
    Returns LayerURI(layer_type="vector", role="context", units=None).
    """
```

**Implementation**:
- URL: `https://www.ncei.noaa.gov/data/storm-events/csvfiles/StormEvents_details-ftp_v1.0_d{year}_c{processed_date}.csv.gz` — processed_date is volatile, must scrape the index page or hardcode latest stable
- Strategy A: use the simpler bulk URL `https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/` (HTTP directory listing)
- Read gzip CSV → DataFrame (pandas); filter on STATE column (case-insensitive); filter on EVENT_TYPE if provided
- Convert to FlatGeobuf using `BEGIN_LAT`/`BEGIN_LON` as point geometry; drop rows with null coords
- Properties: EVENT_ID, EVENT_TYPE, STATE, BEGIN_DATE_TIME, END_DATE_TIME, INJURIES_DIRECT, DAMAGE_PROPERTY, EPISODE_NARRATIVE
- Cache TTL: `static-30d` (historic data is stable)
- Cache key: SHA-256 of (year, state, event_types sorted)
- HTTP: httpx sync timeout=120 (CSV gzip can be 50MB), typed `StormEventsUpstreamError(retryable=True)`

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked: synthetic 100-row CSV → 100 points
- state='FL' filter narrows to ~10 rows
- event_types=['Hurricane'] further narrows
- Year 2022 fixture has Hurricane Ian rows
- Null-coord rows dropped without error
- Live (env GRACE2_TEST_LIVE_STORM=1): year=2022, state='FL' returns >0 features

**Live verification**: fetch_storm_events_db(2022, state='FL', event_types=['Hurricane']) → real FlatGeobuf containing Hurricane Ian + other 2022 FL events; evidence/storm_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_storm_events_db.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_storm_events_db.py` (NEW)
- `reports/inflight/job-0091-engine-20260608/`


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

