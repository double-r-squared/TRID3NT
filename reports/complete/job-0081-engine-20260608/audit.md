# Audit: `compute_slope` atomic tool (DEM slope raster)

**Job ID:** job-0081-engine-20260608, **Sprint:** sprint-11 Stage 1 parallel, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites (ALL APPROVED):**
- job-0033 + job-0039: existing atomic-tool pattern + FR-DC cache shim
- Concurrent job-0079 (`compute_hillshade`) + job-0080 (`compute_colored_relief`): same shape — DEM-input GDAL-wrapped derivative tools

**SRS references:** FR-TA-2, FR-CE-8, FR-DC. DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_dem.py` — pattern reference
- One of the in-flight tools at `services/agent/src/grace2_agent/tools/compute_hillshade.py` OR `compute_colored_relief.py` (if landed by your start time) — mirror the cache+subprocess+LayerURI pattern; otherwise read those kickoffs
- `services/agent/src/grace2_agent/tools/cache.py`
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py`

### Why this job exists

Slope is the third most universally useful DEM-derivative (after hillshade and color-relief). Used by hazard analyses (landslide, urban planning, evacuation routes), future engine inputs, and as a standalone "show me steepness" layer. Per user direction 2026-06-08 — bundle adjacent atomic tools in parallel for sprint-11.

### Scope

NEW file: `services/agent/src/grace2_agent/tools/compute_slope.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",  # DEM-derived; stable
    source_class="slope",
)
def compute_slope(
    dem_uri: str,
    output_unit: Literal["degrees", "percent"] = "degrees",
    algorithm: Literal["Horn", "ZevenbergenThorne"] = "Horn",
) -> LayerURI:
    """Compute terrain slope from a DEM. Wraps `gdaldem slope`.

    output_unit:
        "degrees": slope in degrees (0 = flat, 90 = vertical). Common for cartography.
        "percent": slope in % (rise/run × 100). Common for road grade, engineering.

    algorithm:
        "Horn": default GDAL. 3×3 gradient. Generally accurate.
        "ZevenbergenThorne": alternative gradient. Smoother on rough terrain.

    LLM guidance:
        - Default to "degrees" output. Pick "percent" when user mentions road grade / engineering / construction.
        - Default to "Horn" algorithm. Pick "ZevenbergenThorne" if user mentions rough terrain or noisy DEM.
    """
```

**Implementation:**
- Cache key on `(dem_uri, output_unit, algorithm)`
- On miss: download DEM → run `gdaldem slope -p` (if percent) or default (degrees) with `-alg` flag → upload result
- Return `LayerURI` pointing at the slope GeoTIFF

**Tests:** unit tests against synthetic DEM (32×32 with known gradient → known slope values); cache hit verification.

**Register in registry:** add eager import to `main.py` + symbol export in `tools/__init__.py`.

**Verify:** `--startup-only` should show `compute_slope` in the tool list.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/compute_slope.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — add 1 line
- `services/agent/src/grace2_agent/main.py` — add 1 line
- `services/agent/tests/test_compute_slope.py` (NEW)
- `reports/inflight/job-0081-engine-20260608/`

### FROZEN

- All other tools/* (especially `compute_hillshade.py` from 0079, `compute_colored_relief.py` from 0080, `publish_layer.py`)
- All workflows/*, services/workers/, packages/contracts/, web/, infra/, docs/srs/, styles/
- `reports/complete/**`

### Concurrency note

Concurrent jobs 0079 + 0080 also touch `main.py` + `tools/__init__.py`. Each adds 1 line. If git surfaces textual conflict at commit time, append your line at end + orchestrator will reconcile in close.

### Acceptance criteria

- [ ] `compute_slope` registered + visible at `--startup-only`
- [ ] degrees + percent output unit + Horn + ZevenbergenThorne algorithm combos work (unit tests against synthetic DEM)
- [ ] Cache integration verified
- [ ] Live verification on a real DEM
- [ ] No FROZEN edits
- [ ] Single commit
