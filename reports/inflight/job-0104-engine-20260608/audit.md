# Audit: `fetch_goes_satellite` atomic tool

**Job ID:** job-0104-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_goes_satellite.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",
    source_class="goes_satellite",
    supports_global_query=False,  # NEW Wave 1.5 metadata
)
def fetch_goes_satellite(bbox: tuple[float,float,float,float], band: str = 'visible', satellite: str = 'goes-16') -> LayerURI:
    """GOES-16/17 satellite imagery fetcher for cloud distribution.

    Wraps NOAA GOES-R series via the public S3 bucket. Bands: 'visible' (band 2),
    'ir_window' (band 13), 'water_vapor' (band 8). bbox REQUIRED (full disk = 50MB+
    per band). Tier-1 free, no auth."""
```

**Implementation**:
- Source: `s3://noaa-goes16/ABI-L2-CMIPC/` (CONUS sectorized) or `noaa-goes16/ABI-L1b-RadC/` (raw radiances)
- Use L2 Cloud and Moisture Imagery Product (CMIP) for visible-light renders
- Strategy: fetch most-recent file matching band → netCDF read → reproject to EPSG:4326 → bbox clip → write COG
- Cache key: SHA-256 of (bbox, band, satellite, valid_time_rounded_15min)
- Cache prefix: cache/dynamic-1h/goes/<hash>.tif
- supports_global_query=False (full disk too large; bbox required)
- Raise BBOX_REQUIRED if bbox is None
- LayerURI(layer_type="raster", role="context", units="reflectance"|"K")

**Payload estimation**: estimate_payload_mb: full disk ~50MB; CONUS ~10MB; bbox-clip scales linearly with area / CONUS-area.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked netCDF → GeoTIFF roundtrip
- band='ir_window' vs 'visible' produce different cache keys
- bbox=None raises typed BBOX_REQUIRED error
- Bbox covering open ocean → still returns raster (just dark cells)
- Live (env GRACE2_TEST_LIVE_GOES=1): bbox over FL → real GeoTIFF

**Live verification**: fetch_goes_satellite((-82,26,-80,28), band='visible') → real GeoTIFF; evidence/goes_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_goes_satellite.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_goes_satellite.py` (NEW)
- `reports/inflight/job-0104-engine-20260608/`


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

