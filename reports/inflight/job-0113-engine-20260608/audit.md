# Audit: `fetch_gcn250_curve_numbers` atomic tool

**Job ID:** job-0113-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_gcn250_curve_numbers.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="gcn250_curve_numbers",
    supports_global_query=False,  # NEW Wave 1.5 metadata
)
def fetch_gcn250_curve_numbers(bbox: tuple[float,float,float,float], antecedent_moisture: str = 'average') -> LayerURI:
    """GCN250 Global Curve Numbers for infiltration / runoff modeling.

    GCN250 is a global ~250m grid of SCS curve numbers derived from MODIS+SoilGrids,
    research-validated as the infiltration substrate for compound-flood SFINCS
    workflows (NHESS 2023 Eilander et al). Tier-1 free, no auth."""
```

**Implementation**:
- Source: Zenodo dataset (Jaafar et al 2019) — direct download URLs hosted as COGs
- URL pattern: `https://zenodo.org/record/2532915/files/GCN250_{antecedent_moisture}.tif`
- antecedent_moisture options: 'dry' (AMC-I), 'average' (AMC-II), 'wet' (AMC-III)
- Strategy: rasterio window-read with bbox → write GeoTIFF
- supports_global_query=False (1° squares are fine; CONUS or larger is huge)
- ttl_class="static-30d"
- LayerURI(layer_type="raster", role="primary", units="curve_number")

**Payload estimation**: estimate_payload_mb: 1° square GCN250 at 250m ~5MB; small bbox ~0.5MB.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked CN raster → bbox-clipped
- antecedent_moisture='dry'/'wet' give different CNs at same location
- bbox over ocean → minimal valid CNs
- Unknown moisture state raises typed error
- Live (env GRACE2_TEST_LIVE_GCN=1): Fort Myers bbox returns CN raster mean 60-85 (urban)

**Live verification**: fetch_gcn250_curve_numbers((-82,26,-81,27)) → real CN GeoTIFF; evidence/gcn250_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_gcn250_curve_numbers.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_gcn250_curve_numbers.py` (NEW)
- `reports/inflight/job-0113-engine-20260608/`


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

