# Audit: `compute_colored_relief` atomic tool (hillshade companion)

**Job ID:** job-0080-engine-20260608, **Sprint:** sprint-11 Stage 1 parallel, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites (ALL APPROVED):**
- job-0033 + job-0039: existing atomic-tool pattern + FR-DC cache shim
- job-0062: `publish_layer` atomic tool + worker integration
- Concurrent job-0079 (engine, in flight): `compute_hillshade` atomic tool with same pattern — your tool is the cartographic companion that pairs with hillshade for the swiss-style terrain stack

**SRS references:** FR-TA-2, FR-CE-8, FR-DC. DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_dem.py` — pattern reference
- The in-flight `services/agent/src/grace2_agent/tools/compute_hillshade.py` if it exists by the time you start (otherwise the kickoff at `reports/inflight/job-0079-engine-20260608/audit.md`) — mirror its shape (cache integration, style preset enum, subprocess to `gdaldem`, LayerURI return)
- `services/agent/src/grace2_agent/tools/cache.py` — read_through cache contract
- `services/agent/src/grace2_agent/tools/__init__.py` + `main.py` — registration sites

### Why this job exists

Hillshade gives terrain shape; color-relief gives terrain elevation magnitude. The cartographic stack (basemap + colorramp + hillshade-multiply blend + flood overlay) is what makes Swiss-style maps readable. Per user direction 2026-06-08, pulling adjacent atomic tools forward to parallelize with 0079's hillshade work.

### Scope

NEW file: `services/agent/src/grace2_agent/tools/compute_colored_relief.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",  # DEM-derived; stable
    source_class="colored_relief",
)
def compute_colored_relief(
    dem_uri: str,
    ramp: Literal["terrain", "elevation_blue_green", "grayscale", "viridis"] = "terrain",
) -> LayerURI:
    """Color-tint a DEM by elevation. Wraps `gdaldem color-relief`.

    Ramp presets:
        "terrain": natural-earth green→brown→white (low→high). Default.
        "elevation_blue_green": ocean-blue at sea-level → green→tan→white at high elevations.
        "grayscale": monochrome (multiply-blend companion for hillshade).
        "viridis": perceptually-uniform color ramp (sci-vis style).

    LLM guidance:
        - "terrain" for natural maps
        - "grayscale" when stacking with hillshade in a multiply blend
        - "viridis" when the user wants scientific/quantitative emphasis
        - "elevation_blue_green" when the user mentions ocean / sea / coastal
    """
```

**Implementation:**
- Cache key on `(dem_uri, ramp)`
- On miss: download DEM → write a temp ramp file (CSV format `gdaldem color-relief` reads) → run subprocess → upload result
- Ramp definitions hard-coded inline OR as separate `.txt` files in `styles/ramps/`
- Return `LayerURI` pointing at the colored-relief GeoTIFF

**Tests:** unit tests against synthetic small DEM (32×32 known gradient) per ramp preset; cache-hit verification.

**Register in registry:** add eager import to `main.py` + symbol export in `tools/__init__.py`.

**Verify:** `--startup-only` should show `compute_colored_relief` in the tool list (alongside `compute_hillshade` if 0079 has landed; otherwise just yours).

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/compute_colored_relief.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — add 1 line (re-export)
- `services/agent/src/grace2_agent/main.py` — add 1 line (eager import)
- `services/agent/tests/test_compute_colored_relief.py` (NEW)
- `styles/ramps/*.txt` (NEW if you go that route) — optional
- `reports/inflight/job-0080-engine-20260608/`

### FROZEN

- All other tools/* (especially `compute_hillshade.py` from concurrent 0079, and `publish_layer.py`)
- All workflows/*, services/workers/, packages/contracts/, web/, infra/, docs/srs/, styles/* (except styles/ramps/ if you add it)
- `reports/complete/**`

### Concurrency note

Concurrent job-0079 (`compute_hillshade`) also touches `main.py` + `tools/__init__.py`. Likely single-line additions in each. Use `with open(..., "a")` patterns OR append at the end of import sections. If git surfaces a textual conflict, leave both lines + commit; orchestrator will reconcile in close.

### Acceptance criteria

- [ ] `compute_colored_relief` registered + visible at `--startup-only`
- [ ] 4 ramp presets work (unit tests against synthetic DEM)
- [ ] Cache integration verified (read_through + cache hit)
- [ ] Live verification on a real DEM (e.g., job-0075's cached Fort Myers DEM)
- [ ] No FROZEN edits
- [ ] Single commit
