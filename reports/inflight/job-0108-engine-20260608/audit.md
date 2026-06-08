# Audit: `fetch_firms_active_fire` atomic tool

**Job ID:** job-0108-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_firms_active_fire.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",
    source_class="firms_active_fire",
    supports_global_query=False,  # NEW Wave 1.5 metadata
)
def fetch_firms_active_fire(bbox: tuple[float,float,float,float], days_back: int = 1, source: str = 'VIIRS_SNPP_NRT') -> LayerURI:
    """NASA FIRMS active fire / thermal anomaly detections.

    Wraps NASA FIRMS Web Service for satellite-detected active fires. Returns
    FlatGeobuf points with brightness/FRP/confidence. Tier-1 free Map API (no key
    required for AREA endpoint)."""
```

**Implementation**:
- Endpoint: `https://firms.modaps.eosdis.nasa.gov/api/area/csv/<MAP_KEY>/<source>/<bbox>/<days>`
- MAP_KEY: NASA FIRMS Map Key — free public registration; for v0.1 use 'demo' key (rate-limited); surface OQ-108-MAP-KEY-AUTH for production
- Available sources: VIIRS_SNPP_NRT (default, NPP), VIIRS_NOAA20_NRT, MODIS_NRT
- days_back: 1-10 supported by FIRMS
- bbox format: 'west,south,east,north'
- Response is CSV → parse → FlatGeobuf points
- Properties: latitude, longitude, brightness, scan, track, acq_date, acq_time, confidence, frp, daynight
- supports_global_query=False (FIRMS requires bbox)
- ttl_class="dynamic-1h"
- LayerURI(layer_type="vector", role="primary")

**Payload estimation**: estimate_payload_mb: ~0.1MB per 1000 points; CONUS busy day ~5MB; quiet day ~0.5MB.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked CSV → FlatGeobuf
- Bbox bounds validation
- Empty response → 0-feature FlatGeobuf
- Unknown source raises typed error
- Live (env GRACE2_TEST_LIVE_FIRMS=1): CA bbox last 7 days → ≥0 features

**Live verification**: fetch_firms_active_fire((-122,38,-119,40), days_back=7) → real FlatGeobuf; evidence/firms_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_firms_active_fire.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_firms_active_fire.py` (NEW)
- `reports/inflight/job-0108-engine-20260608/`


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

