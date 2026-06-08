# Audit: `fetch_nws_alerts_conus` atomic tool

**Job ID:** job-0105-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_nws_alerts_conus.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",
    source_class="nws_alerts_conus",
    supports_global_query=True,  # NEW Wave 1.5 metadata
)
def fetch_nws_alerts_conus(event_types: list[str] | None = None, status: str = 'actual') -> LayerURI:
    """NWS active weather alerts — CONUS-wide companion to fetch_nws_event (job-0090).

    fetch_nws_event takes area=state|bbox; this sibling fetches ALL active CONUS alerts
    in one call for 'show me warnings across America' use cases. ~500 active alerts
    nationwide, small payload (~200KB)."""
```

**Implementation**:
- Endpoint: `https://api.weather.gov/alerts/active?status={status}` (no area filter)
- Headers: User-Agent REQUIRED per NWS API (`grace2-agent/0.1 ...`)
- event_types filter: client-side after fetch
- ttl_class="dynamic-1h"
- supports_global_query=True (CONUS-default; this IS the CONUS-wide variant)
- Returns FlatGeobuf polygons (alerts often carry polygons)
- LayerURI(layer_type="vector", role="primary")

**Payload estimation**: estimate_payload_mb: ~0.2MB CONUS (500 alerts × ~400 bytes each).

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked: 50-alert response → 50 features
- event_types filter narrows
- Cache miss/hit
- User-Agent header verified
- Live (env GRACE2_TEST_LIVE_NWS_CONUS=1): returns ≥0 features (could be 0 if all quiet)

**Live verification**: fetch_nws_alerts_conus() → real FlatGeobuf with current CONUS alerts; evidence/nws_conus_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_nws_alerts_conus.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_nws_alerts_conus.py` (NEW)
- `reports/inflight/job-0105-engine-20260608/`


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

