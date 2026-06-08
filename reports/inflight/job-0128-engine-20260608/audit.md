# Audit: `fetch_ebird_observations` atomic tool

**Job ID:** job-0128-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (Wave 1 pattern)
- `services/agent/src/grace2_agent/tools/cache.py`
- `packages/contracts/src/grace2_contracts/secrets.py` (SecretRecord shape)
- `services/agent/src/grace2_agent/persistence.py` (Persistence class for secret lookup)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_ebird_observations.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",
    source_class="ebird",
    supports_global_query=False,
)
def fetch_ebird_observations(species_code: str, bbox: tuple[float,float,float,float], days_back: int = 30, api_key: str | None = None, secret_ref: dict | None = None) -> LayerURI:
    """Cornell Lab eBird recent observations Tier-2 fetcher.

    Wraps the eBird API (https://api.ebird.org/v2/). Returns FlatGeobuf points
    with sighting date + locality + observer + count + comments. Requires eBird API key."""
```

**Implementation**:
- Endpoint: `https://api.ebird.org/v2/data/obs/geo/recent/{species_code}?lat={lat}&lng={lon}&dist=50&back={days_back}&fmt=json`
- Headers: `X-eBirdApiToken: {api_key}`
- bbox center → lat/lon; eBird supports radius queries (dist in km)
- For full bbox coverage: tile the bbox into ~50km circles via simple grid (sprint-13 optimization: hex tiling)
- Properties: subId, obsDt, locName, howMany, lat, lng, comName, sciName
- Cache: dynamic-1h (eBird updates rapidly)
- Cache key: SHA-256 of (species_code, bbox-rounded-6dp, days_back)
- supports_global_query=False (bbox required; species are mobile and sparse)


**Tier-2 secret handling**: this tool requires a `ebird` API key. Accept via:
- `api_key: str | None = None` parameter (explicit)
- OR `secret_ref: SecretRecord | None = None` (lookup via Persistence.get_secret_value(secret_ref) — Wave 2 sibling job-0124 lands this method)
- Fallback: `os.environ.get("GRACE2_EBIRD_API_KEY")` for local dev

Unit tests use mocked HTTP responses (no real key needed); live test (env-gated `GRACE2_TEST_LIVE_EBIRD=1` + env var with real key) verifies live response. **Mark live test as `pytest.mark.skipif` based on key availability — do NOT fail unit suite if key is missing.**


**Payload estimation**: estimate_payload_mb: ~0.05MB per 1000 observations; species + bbox scales modestly.

**Tests** (≥4 unit + ≥1 live env-guarded):
- Mocked eBird response → FlatGeobuf points
- Multiple tiles for large bbox → deduplication of overlapping observations
- Empty response → 0-feature FlatGeobuf
- Bad species_code → typed input error
- Live (env GRACE2_TEST_LIVE_EBIRD=1 + GRACE2_EBIRD_API_KEY): bewickwren bbox over CA → ≥0 features

**Live verification** (env-guarded): fetch_ebird_observations('bewwre', (-122,38,-119,40)) → real FlatGeobuf; evidence/ebird_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_ebird_observations.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_ebird_observations.py` (NEW)
- `reports/inflight/job-0128-engine-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 2 job's exclusive files; `reports/complete/**`; `docs/SRS_v0.3.md` monolith (regenerated only); all Wave 1/1.5 atomic tool files (additive use only — don't modify their signatures).

### Concurrency note (Wave 2 fan-out — 16 parallel)

Same idempotent-append pattern + `git pull --rebase` pre-commit mitigation as Wave 1.5. Files all land correctly in HEAD; only commit-message labels may drift. Use marker commits if your changes get swept into a sibling's commit hash.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: verify against real geography, not URL/render consistency.
2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: ALL CRUD goes through `Persistence.*`. Do NOT design custom collection wrappers. If your job needs a new method on Persistence, ADD it (additive) rather than bypassing.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness / behavioral-correctness verified
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

