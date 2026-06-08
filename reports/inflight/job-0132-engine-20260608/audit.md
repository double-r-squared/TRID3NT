# Audit: `fetch_gtsm_tide_surge` atomic tool

**Job ID:** job-0132-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (Wave 1 pattern)
- `services/agent/src/grace2_agent/tools/cache.py`
- `packages/contracts/src/grace2_contracts/secrets.py` (SecretRecord shape)
- `services/agent/src/grace2_agent/persistence.py` (Persistence class for secret lookup)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_gtsm_tide_surge.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="gtsm",
    supports_global_query=False,
)
def fetch_gtsm_tide_surge(bbox: tuple[float,float,float,float], start_date: str, end_date: str, output: str = 'water_level', api_key: str | None = None, secret_ref: dict | None = None) -> LayerURI:
    """Global Tide and Surge Model v3.0 Tier-2 fetcher.

    GTSM (Deltares) — coastal water level forcing combining tide + surge.
    Research-validated as the compound-flood coastal boundary (Eilander 2023).
    Accessed via Copernicus Climate Data Store."""
```

**Implementation**:
- Use cdsapi (same as ERA5 sibling job-0131)
- Dataset: `sis-water-level-change-timeseries-cmip6` or comparable
- Output points are gauge-station-based: returns nearest gauge to bbox + time series, OR rasterized water-level field
- Cache: static-30d
- Cache key on (bbox, start_date, end_date, output)
- supports_global_query=False (bbox preferred for time-series; full global is huge)


**Tier-2 secret handling**: this tool requires a `copernicus_cds` API key. Accept via:
- `api_key: str | None = None` parameter (explicit)
- OR `secret_ref: SecretRecord | None = None` (lookup via Persistence.get_secret_value(secret_ref) — Wave 2 sibling job-0124 lands this method)
- Fallback: `os.environ.get("GRACE2_COPERNICUS_CDS_API_KEY")` for local dev

Unit tests use mocked HTTP responses (no real key needed); live test (env-gated `GRACE2_TEST_LIVE_COPERNICUS_CDS=1` + env var with real key) verifies live response. **Mark live test as `pytest.mark.skipif` based on key availability — do NOT fail unit suite if key is missing.**


**Payload estimation**: estimate_payload_mb: ~0.1MB per day per coastal bbox.

**Tests** (≥4 unit + ≥1 live env-guarded):
- Mocked cdsapi → netCDF → CSV time-series output
- output='water_level' vs 'surge_only'
- Date range validation
- Live (env GRACE2_TEST_LIVE_GTSM=1 + CDS key): Florida coast bbox + Hurricane Ian dates → real time-series

**Live verification** (env-guarded): fetch_gtsm_tide_surge((-83,25,-80,28), '2022-09-26','2022-09-29') → real time-series; evidence/gtsm_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_gtsm_tide_surge.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_gtsm_tide_surge.py` (NEW)
- `reports/inflight/job-0132-engine-20260608/`


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

