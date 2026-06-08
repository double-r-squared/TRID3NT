# Audit: `compute_building_density` atomic tool

**Job ID:** job-0096-engine-20260608, **Sprint:** sprint-12-mega Wave 1 (parallel fan-out), **Auditor:** Development Orchestrator, **Status:** assigned

**Specialist:** engine

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_administrative_boundaries.py` — pattern reference (job-0084)
- `services/agent/src/grace2_agent/tools/cache.py` — `read_through` shim
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Scope

NEW file `services/agent/src/grace2_agent/tools/compute_building_density.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",
    source_class="building_density",
)
def compute_building_density(bbox: tuple[float,float,float,float], cell_size_m: float = 100.0, source: str = 'ms_footprints') -> LayerURI:
    """Microsoft Global ML Building Footprints density raster.

    Fetches building footprints from Microsoft's Global ML Building Footprints
    dataset (S3 public bucket; FGB tiles by quadkey), rasterizes to a grid at the
    requested cell_size_m, returns count-per-cell as float32 COG.
    Returns LayerURI(layer_type="raster", role="context", units=None).
    """
```

**Implementation**:
- Source: Microsoft Global ML Building Footprints (https://github.com/microsoft/GlobalMLBuildingFootprints) — quadkey-tiled FGBs at usbuildings-v2.amazonaws.com
- For bbox: compute intersecting quadkeys at zoom-9 (the dataset's native tiling), download each FGB
- Strategy v0.1: just the US dataset (us-buildings-v2). For non-US bboxes, surface OQ-96-INTL-COVERAGE
- Rasterize building polygons to a regular grid at `cell_size_m` in EPSG:3857 (preserves area metric)
- Output: float32 COG, count of building centroids per cell
- Cache key on (bbox-rounded-6dp, cell_size_m, source)
- Cache prefix: cache/static-30d/building_density/<hash>.tif

**Tests** (≥4 unit + ≥1 live, env-guarded):
- Mocked FGB with 100 building polygons in a 1km² bbox → density grid with sum=100
- cell_size_m=50 vs 200 produces correctly-scaled outputs
- Empty bbox → zero raster, no error
- Cache miss/hit
- Live (env GRACE2_TEST_LIVE_BUILDINGS=1): Fort Myers bbox → density raster

**Live verification**: compute_building_density((-82.0, 26.5, -81.8, 26.7), 100) → real density COG over Fort Myers; evidence/building_density_live.txt

**Register**: `tools/__init__.py` + `main.py` 1 line each. Verify via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/compute_building_density.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line (idempotent-append)
- `services/agent/src/grace2_agent/main.py` — 1 line (idempotent-append)
- `services/agent/tests/test_compute_building_density.py` (NEW)
- `reports/inflight/job-0096-engine-20260608/`


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

