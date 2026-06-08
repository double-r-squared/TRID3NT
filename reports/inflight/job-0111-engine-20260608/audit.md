# Audit: `fetch_landfire_fuels` atomic tool

**Job ID:** job-0111-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_landfire_fuels.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="landfire_fuels",
    supports_global_query=False,  # NEW Wave 1.5 metadata
)
def fetch_landfire_fuels(bbox: tuple[float,float,float,float], layer: str = 'fbfm40') -> LayerURI:
    """LANDFIRE fuels and vegetation raster fetcher.

    Wraps the LANDFIRE Data Distribution Site WMS-like API. Returns GeoTIFF
    with fuel model classes. Tier-1 free, no auth. Useful for wildfire risk
    analysis (input to FlamMap, FARSITE)."""
```

**Implementation**:
- Endpoint: `https://lfps.usgs.gov/arcgis/rest/services/LandfireProductService/GPServer/LandfireProductService/execute`
- This is an async GP service: submit job → poll → download result raster
- Alternative simpler path: pre-staged LANDFIRE 2020 mosaics from `https://landfire.gov/data_overviews.php`
- layer options: 'fbfm40' (40 Scott-Burgan fuel model), 'fbfm13' (13 Anderson), 'cbh' (canopy base height), 'cbd' (canopy bulk density)
- v0.1 strategy: hard-code one LANDFIRE 2022 nationwide COG URL per layer; use rasterio window-read with bbox clip
- supports_global_query=False (bbox required)
- ttl_class="static-30d"
- LayerURI(layer_type="raster", role="primary")

**Payload estimation**: estimate_payload_mb: scales with bbox area; 1° square ~5MB at LANDFIRE 30m.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked LANDFIRE raster → bbox-clipped output
- layer='fbfm13' vs 'fbfm40' produce different cache keys
- bbox over open ocean → zero raster, no error
- Unknown layer raises typed error
- Live (env GRACE2_TEST_LIVE_LANDFIRE=1): CA bbox returns real raster

**Live verification**: fetch_landfire_fuels((-122,38,-119,40), layer='fbfm40') → real GeoTIFF; evidence/landfire_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_landfire_fuels.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_landfire_fuels.py` (NEW)
- `reports/inflight/job-0111-engine-20260608/`


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

