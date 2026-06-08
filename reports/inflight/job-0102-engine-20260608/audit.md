# Audit: `fetch_nexrad_reflectivity` atomic tool

**Job ID:** job-0102-engine-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** engine, **Status:** assigned

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` (pattern reference)
- `services/agent/src/grace2_agent/tools/cache.py` (`read_through` shim)

### Scope

NEW file `services/agent/src/grace2_agent/tools/fetch_nexrad_reflectivity.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="live-no-cache",
    source_class="nexrad",
    supports_global_query=True,  # NEW Wave 1.5 metadata
)
def fetch_nexrad_reflectivity(bbox: tuple[float,float,float,float] | None = None, product: str = 'n0r') -> LayerURI:
    """NEXRAD composite radar reflectivity via Iowa Mesonet WMS.

    Wraps the Iowa State University Mesonet NEXRAD WMS endpoint (no auth). Returns
    a LayerURI pointing at a WMS service URL the client can render directly — does
    NOT cache pixels (the WMS is dynamic). product: n0r=composite reflectivity (default),
    n0q=base reflectivity, vil=vertically integrated liquid. bbox=None displays CONUS."""
```

**Implementation**:
- WMS endpoint: `https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/{product}.cgi`
- Available products: n0r (composite reflectivity), n0q (base reflectivity tilt 0.5°), vil (vertically integrated liquid)
- This tool does NOT download pixels — it composes the WMS URL with the product + bbox and returns LayerURI pointing at the service. Client renders tiles on demand via standard WMS.
- supports_global_query=True (CONUS-default; bbox=None means full CONUS)
- ttl_class="live-no-cache" (radar updates every 5 min)
- LayerURI(layer_type="raster", role="context", attribution="NEXRAD via Iowa State Mesonet")

**Payload estimation**: Always near-zero (WMS service URL, not a payload). Set estimate_payload_mb to return 0.1.

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Default product n0r returns LayerURI with correct WMS URL
- product='n0q' produces a different LayerURI
- product='vil' produces a different LayerURI
- bbox=None → LayerURI with no bbox restriction
- bbox=(-82,26,-81,27) → LayerURI carrying that bbox
- Unknown product → typed error

**Live verification**: fetch_nexrad_reflectivity() → LayerURI; HEAD request to the WMS endpoint to confirm reachable; evidence/nexrad_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/fetch_nexrad_reflectivity.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append with rebase mitigation)
- `services/agent/tests/test_fetch_nexrad_reflectivity.py` (NEW)
- `reports/inflight/job-0102-engine-20260608/`


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

