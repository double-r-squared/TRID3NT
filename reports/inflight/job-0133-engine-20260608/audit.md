# Audit: `fetch_cama_flood_discharge` atomic tool

**Job ID:** job-0133-engine-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_gbif_occurrences.py` (Wave 1 pattern)
- `services/agent/src/grace2_agent/tools/cache.py`
- `packages/contracts/src/grace2_contracts/secrets.py` (SecretRecord shape)


### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_cama_flood_discharge.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="cama_flood",
    supports_global_query=False,
)
def fetch_cama_flood_discharge(bbox: tuple[float,float,float,float], start_date: str, end_date: str, version: str = 'v4.0.1') -> LayerURI:
    """CaMa-Flood river discharge Tier-1 fetcher.

    CaMa-Flood (Yamazaki, Univ of Tokyo) — global river routing + discharge.
    Research-validated as compound-flood fluvial forcing (Eilander 2023).
    Public release via U.Tokyo Hydra server; no auth required for v4.0.1."""
```

**Implementation**:
- Source: U.Tokyo Hydra server (no auth) — netCDF files for daily global discharge at 10km resolution
- Path: `https://hydro.iis.u-tokyo.ac.jp/~yamadai/cama-flood/CaMa-Flood_v4/data/runoff/`
- Strategy: download netCDF for date range, clip to bbox, write GeoTIFF
- Cache: static-30d
- Cache key on (bbox, start_date, end_date, version)
- supports_global_query=False (clip recommended; 10km global = ~500MB)
- LayerURI(layer_type="raster", role="primary", units="m^3/s")



**Payload estimation**: estimate_payload_mb: ~1MB per day per 1° square at 10km native res.

**Tests** (≥4 unit + ≥1 live env-guarded):
- Mocked netCDF → bbox-clipped GeoTIFF
- Multi-day range produces single composite OR per-day outputs
- Date range validation
- Live (env GRACE2_TEST_LIVE_CAMA=1): Mississippi basin bbox + recent date → real discharge raster

**Live verification** (env-guarded): fetch_cama_flood_discharge((-92,30,-89,32), '2024-09-01','2024-09-01') → real GeoTIFF; evidence/cama_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_cama_flood_discharge.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_fetch_cama_flood_discharge.py` (NEW)
- `reports/inflight/job-0133-engine-20260608/`


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

