# Audit: `fetch_hrsl_population` atomic tool

**Job ID:** job-0112-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_hrsl_population.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="hrsl_population",
    supports_global_query=False,  # NEW Wave 1.5 metadata
)
def fetch_hrsl_population(bbox: tuple[float,float,float,float], year: int = 2020, source: str = 'meta_hrsl') -> LayerURI:
    """High-resolution settlement layer (HRSL) population fetcher.

    Wraps the Meta + CIESIN HRSL dataset (1 arcsec resolution, persons/cell)
    OR Worldpop HRSL. Tier-1 free, no auth for v0.1 (Meta HRSL on AWS Open Data).
    Useful for Case 1 exposure modeling (population at risk)."""
```

**Implementation**:
- Source: `s3://dataforgood-fb-data/hrsl-cogs/` (Meta HRSL COGs by country)
- v0.1 strategy: country-tile pattern — derive country from bbox center (use a hardcoded US-only path for v0.1; surface OQ-112-INTL for international)
- US HRSL COG path: `s3://dataforgood-fb-data/hrsl-cogs/hrsl_general/v1.5.0/hrsl_general-latest.tif` (whole-US, ~100MB)
- Strategy: rasterio window-read with bbox → write GeoTIFF
- supports_global_query=False (bbox required; full US too big)
- ttl_class="static-30d"
- LayerURI(layer_type="raster", role="primary", units="persons_per_cell")

**Payload estimation**: estimate_payload_mb: 1° square HRSL ~3MB at 1 arcsec; small city ~0.5MB.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked HRSL raster → bbox-clipped output
- bbox over ocean → mostly nodata
- bbox=None raises BBOX_REQUIRED
- Cache miss/hit
- Live (env GRACE2_TEST_LIVE_HRSL=1): Fort Myers bbox returns population raster

**Live verification**: fetch_hrsl_population((-82,26,-81,27)) → real population GeoTIFF; evidence/hrsl_live.txt with sum_population

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_hrsl_population.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_hrsl_population.py` (NEW)
- `reports/inflight/job-0112-engine-20260608/`


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

