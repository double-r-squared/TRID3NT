# Audit: `compute_impervious_surface` atomic tool

**Job ID:** job-0095-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/compute_impervious_surface.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="impervious",
)
def compute_impervious_surface(landcover_uri: str, bbox: tuple[float,float,float,float] | None = None) -> LayerURI:
    """NLCD impervious-surface fraction computation.

    Reads NLCD impervious-surface raster (separate USGS product from NLCD landcover).
    OR if given NLCD landcover, derives impervious surface from developed-class membership
    (21=Open Space 0%, 22=Low 30%, 23=Medium 60%, 24=High 90%). Returns float32 raster
    of impervious fraction 0.0-1.0.
    Returns LayerURI(layer_type="raster", role="context", units=None).
    """
```

**Implementation**:
- Two paths:
  - If landcover_uri points to NLCD Impervious Surface product (auto-detect via filename heuristic or rasterio.tags()): direct read, scale 0-100 → 0.0-1.0
  - Else (NLCD Landcover product): derive — developed_class_to_impervious = {21:0.0, 22:0.3, 23:0.6, 24:0.9, default:0.0}
- Output: float32 COG, nodata=NaN
- Cache key on (landcover_uri, bbox)
- Cache prefix: cache/static-30d/impervious/<hash>.tif

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Synthetic landcover raster with classes 22,23,24 → output has 0.3,0.6,0.9
- Synthetic impervious-product raster scale 0-100 → output 0.0-1.0
- nodata preservation
- bbox window
- Cache miss/hit

**Live verification**: compute_impervious_surface on real NLCD landcover → impervious-fraction GeoTIFF; evidence/impervious_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/compute_impervious_surface.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_compute_impervious_surface.py` (NEW)
- `reports/inflight/job-0095-engine-20260608/`


### FROZEN

All other `tools/*` (each Wave 1 sibling has its own file ownership); all `workflows/`, `services/workers/`, `packages/contracts/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`.


### Concurrency note (Wave 1 fan-out)

~15 Wave 1 jobs run concurrently. Each owns its own NEW tool file but ALL share `tools/__init__.py` + `main.py` registration sites. The idempotent-append pattern from sprint-11 Stage 1 (which handled 6 concurrent additions cleanly) applies: ADD your import line at the end of each file; if your line conflicts with a sibling's, do `git pull --rebase` style re-apply; do NOT remove other tool imports.


### Codified lesson (job-0086, do not violate)

URL/render consistency != geographic correctness. In-COG axis mirrors and similar in-file orientation bugs are invisible to every consistency check (server, client, PIL composite all faithfully serve the mirrored array). If your tool emits geometry, your acceptance test MUST verify the output against the **known geography of the bbox** (e.g. "is the deep-flood pixel at the river mouth?"), not just "did the bytes round-trip?".


### Acceptance criteria

- [ ] New tool registered + visible at `--startup-only` (count = entering_count + 1)
- [ ] ≥4 unit tests + 1 live test (with appropriate env-var guard)
- [ ] Live verification with real upstream response captured to evidence/
- [ ] Geography correctness check per the codified job-0086 lesson (where applicable)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence paths + any OQs

