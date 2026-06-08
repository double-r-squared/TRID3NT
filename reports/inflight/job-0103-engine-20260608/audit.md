# Audit: `fetch_mrms_qpe` atomic tool

**Job ID:** job-0103-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_mrms_qpe.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",
    source_class="mrms_qpe",
    supports_global_query=True,  # NEW Wave 1.5 metadata
)
def fetch_mrms_qpe(bbox: tuple[float,float,float,float] | None = None, accumulation: str = '01H', valid_time: str | None = None) -> LayerURI:
    """NOAA MRMS Quantitative Precipitation Estimate fetcher.

    Wraps the NOAA MRMS (Multi-Radar Multi-Sensor) gauge-corrected radar QPE
    product. Returns GeoTIFF raster (mm). Tier-1 free, no auth. Research-validated
    as the Harvey/Houston SFINCS standard precipitation forcing (GMD 2025)."""
```

**Implementation**:
- Source: NOAA MRMS public S3 bucket `s3://noaa-mrms-pds/CONUS/`
- Accumulation products: 01H (1-hour), 03H, 06H, 24H, 48H, 72H (multi-day accumulated)
- Files in grib2 format, ~5MB CONUS hourly
- valid_time: ISO-8601 UTC; if None, fetch the most recent available
- Strategy: download grib2 → rasterio read → write GeoTIFF; if bbox given, clip
- Cache key: SHA-256 of (bbox-rounded-6dp, accumulation, valid_time)
- Cache prefix: cache/dynamic-1h/mrms_qpe/<hash>.tif (recent data) or static-30d for historic
- supports_global_query=True (CONUS-default)
- Returns LayerURI(layer_type="raster", role="primary", units="mm")

**Payload estimation**: estimate_payload_mb: CONUS hourly ~5MB; multi-day ~5MB; bbox-clipped scales by area / CONUS-area.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked grib2 fixture → GeoTIFF output
- accumulation='24H' vs '01H' produce different cache keys
- bbox=None vs bbox→clip behaviors
- Unknown accumulation raises typed error
- Live (env GRACE2_TEST_LIVE_MRMS=1): CONUS 24h accumulation → real GeoTIFF with non-zero values

**Live verification**: fetch_mrms_qpe(accumulation='24H') → real GeoTIFF; evidence/mrms_live.txt with max/mean values

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_mrms_qpe.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_mrms_qpe.py` (NEW)
- `reports/inflight/job-0103-engine-20260608/`


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

