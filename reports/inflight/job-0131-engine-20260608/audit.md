# Audit: `fetch_era5_reanalysis` atomic tool

**Job ID:** job-0131-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (Wave 1 pattern)
- `services/agent/src/grace2_agent/tools/cache.py`
- `packages/contracts/src/grace2_contracts/secrets.py` (SecretRecord shape)
- `services/agent/src/grace2_agent/persistence.py` (Persistence class for secret lookup)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_era5_reanalysis.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="era5",
    supports_global_query=True,
)
def fetch_era5_reanalysis(bbox: tuple[float,float,float,float], variable: str, start_date: str, end_date: str, api_key: str | None = None, secret_ref: dict | None = None) -> LayerURI:
    """Copernicus ERA5 reanalysis Tier-2 fetcher.

    Wraps the Copernicus CDS API (cdsapi) for ERA5 hourly/daily reanalysis.
    Research-validated as the compound-flood global substrate (NHESS 2023).
    Variables: '10m_u_component_of_wind', '10m_v_component_of_wind', '2m_temperature',
    'total_precipitation', 'runoff', 'significant_height_of_combined_wind_waves_and_swell'."""
```

**Implementation**:
- Use Python `cdsapi` package (add to pyproject.toml)
- CDS API URL + key from `~/.cdsapirc` or env vars
- Submit request: variable, area=[N,W,S,E], date range, time, format=netcdf
- CDS jobs are async — poll up to 5 min for completion, download netCDF, convert to COG
- Cache: static-30d (historical reanalysis is stable); cache key on (variable, bbox, start_date, end_date)
- supports_global_query=True (ERA5 is global by default if bbox=None)
- LayerURI(layer_type="raster", role="primary", units varies by variable)


**Tier-2 secret handling**: this tool requires a `copernicus_cds` API key. Accept via:
- `api_key: str | None = None` parameter (explicit)
- OR `secret_ref: SecretRecord | None = None` (lookup via Persistence.get_secret_value(secret_ref) — Wave 2 sibling job-0124 lands this method)
- Fallback: `os.environ.get("GRACE2_COPERNICUS_CDS_API_KEY")` for local dev

Unit tests use mocked HTTP responses (no real key needed); live test (env-gated `GRACE2_TEST_LIVE_COPERNICUS_CDS=1` + env var with real key) verifies live response. **Mark live test as `pytest.mark.skipif` based on key availability — do NOT fail unit suite if key is missing.**


**Payload estimation**: estimate_payload_mb: ~0.5MB per variable per day per 1° square at 0.25° native res.

**Tests** (≥4 unit + ≥1 live env-guarded):
- Mocked cdsapi client → netCDF roundtrip
- variable='total_precipitation' vs 'runoff' produce different cache keys
- Date range validation
- Live (env GRACE2_TEST_LIVE_ERA5=1 + ~/.cdsapirc): small bbox, 1 day, total_precipitation → real GeoTIFF

**Live verification** (env-guarded): fetch_era5_reanalysis((-82,26,-81,27), 'total_precipitation', '2024-09-26', '2024-09-26') → real GeoTIFF; evidence/era5_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_era5_reanalysis.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_era5_reanalysis.py` (NEW)
- `reports/inflight/job-0131-engine-20260608/`


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

