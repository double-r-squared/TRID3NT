# Audit: `compute_hillshade` atomic tool + `publish_terrain_composite` higher-order tool

**Job ID:** job-0079-engine-20260608, **Sprint:** sprint-11 Stage 1 (parallel with alignment fix), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites (ALL APPROVED):**
- job-0033 + job-0039: existing `fetch_dem` atomic tool pattern + cache-shim integration baseline
- job-0062: PyQGIS worker `_append_raster_layer` + `publish-raster` CLI; existing `publish_layer` atomic tool reference (now in production after job-0076)
- job-0075's verified DEM cache hit pattern at Fort Myers

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-TA-2 (atomic tools), FR-CE-8 (cache shim), FR-DC (data caching)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_dem.py` — pattern reference (existing DEM-fetcher)
- `services/agent/src/grace2_agent/tools/publish_layer.py` — pattern reference (existing publish-via-worker tool)
- `services/agent/src/grace2_agent/tools/__init__.py` + `services/agent/src/grace2_agent/main.py` — registration site
- `services/agent/src/grace2_agent/tools/cache.py` — the read_through cache shim contract
- `styles/continuous_flood_depth.qml` — the existing flood QML; you may want to create a parallel `hillshade_*.qml` per style preset

### Why this job exists

User direction 2026-06-08: hillshade-as-atomic-tool bumped up the roadmap (was sprint-12+). It's a fundamental piece of cartographic context — a flood layer over a basemap is harder to read than the same flood layer over a hillshaded terrain. Adding it now (sprint-11 Stage 1) unblocks the Case UX (sprint-11 headline) being able to show meaningful terrain + flood demos.

This is a NEW atomic tool that wraps GDAL's `gdaldem` algorithms with sensible style presets the LLM can pick from natural-language prompts.

### Scope

#### Part 1 — `compute_hillshade` atomic tool

NEW file: `services/agent/src/grace2_agent/tools/compute_hillshade.py`.

```python
@register_tool(
    cacheable=True,
    ttl_class="static-30d",  # DEM-derived; stable
    source_class="hillshade",
)
def compute_hillshade(
    dem_uri: str,
    style: Literal["standard", "swiss_double", "multidirectional", "combined", "smooth"] = "standard",
    # Power-user overrides (only consulted if style="custom"):
    algorithm: Literal["Horn", "ZevenbergenThorne", "Igor"] = "Horn",
    azimuth: float = 315.0,
    altitude: float = 45.0,
    z_factor: float = 1.0,
) -> LayerURI:
    """Compute a hillshade raster from a DEM.

    Wraps `gdaldem hillshade` with predefined style presets the LLM can
    pick from natural-language intent ("make it cartographic" → swiss_double).
    Returns a LayerURI pointing at a single hillshade GeoTIFF (or pair, for
    swiss_double which is computed as two GeoTIFFs that the composite tool
    will multiply-blend server-side).

    Style preset semantics:
        "standard": single hillshade, Horn algorithm, azimuth 315°,
            altitude 45° — the GDAL default. Fast, OK for general use.
        "swiss_double": two hillshades — Horn @ azimuth 315° + Horn @ 135°
            — to be multiply-blended server-side for richer cartographic
            depth (Imhof-style). Best for terrain reading.
        "multidirectional": single hillshade with -multidirectional flag
            — combines NE/SE/NW/SW illuminations, no dead lit sides.
        "combined": -combined flag — brightness incorporates slope steepness;
            best for steep mountainous terrain.
        "smooth": Horn with ZevenbergenThorne algorithm — smoother on
            rough terrain.

    LLM guidance:
        - Pick "swiss_double" when the user asks for "cartographic" /
          "professional" / "nice-looking" terrain
        - Pick "multidirectional" when the user mentions "no dead spots" or
          "see all sides"
        - Pick "combined" for mountains or steep terrain
        - Pick "standard" otherwise (cheaper)
    """
    # implementation:
    # - cache key on (dem_uri, style, algorithm, azimuth, altitude, z_factor)
    # - check cache via read_through; return if hit
    # - on miss: download DEM from gs://, run gdaldem in subprocess, upload result
    # - swiss_double computes TWO hillshades (returns a paired result;
    #   suggest a LayerURI variant or tuple — adapt to existing LayerURI shape)
    pass
```

**Implementation notes:**
- Use `subprocess.run` with `gdaldem hillshade` — already available in `.venv-agent` (job-0063 confirmed pyproj + GDAL)
- Cache key derivation per FR-DC: SHA256 of canonicalized `(dem_uri, style, algorithm, azimuth, altitude, z_factor)`; cache prefix `cache/static-30d/hillshade/<hash>.tif`
- `swiss_double` is tricky for the LayerURI contract — it produces 2 GeoTIFFs that need to be composited. Two reasonable approaches:
  - **A:** return a single LayerURI with `uri` pointing at a pre-composited multiply-blend GeoTIFF (compute the blend in this tool via numpy)
  - **B:** return a LayerURI where the URI points at a directory containing both component GeoTIFFs, and the `publish_terrain_composite` higher-order tool reads both and stacks them server-side
  - Pick A — simpler contract, the LLM-visible result is one layer the user can think about as "the hillshade".

#### Part 2 — `publish_terrain_composite` higher-order tool (optional within this job — surface as OQ-79-* if scope creeps)

This is the "compose hillshade + colorramp + flood into a stack" tool. Implementation:

```python
@register_tool(
    cacheable=False,
    ttl_class="live-no-cache",
    source_class="publish_terrain",
)
def publish_terrain_composite(
    dem_uri: str,
    style: Literal["swiss", "modern", "smooth"] = "swiss",
    colorramp: Literal["terrain", "elevation_blue_green", "grayscale"] = "terrain",
    case_id: str | None = None,  # for per-Case .qgs isolation in sprint-11+
) -> LayerURI:
    """Build a terrain-context composite layer (hillshade + colorramp DEM)
    and publish to QGIS Server as a single WMS layer.

    Internally: calls compute_hillshade(dem_uri, style=<swiss_double|...>) +
    computes color-relief via gdaldem color-relief + mutates the .qgs project
    to add both layers with the correct multiply/normal blend modes set on
    each layer (QGIS supports raster blend modes natively).
    """
    pass
```

**If scope creeps**: split as a sprint-11 follow-up job — surface as OQ-79-PUBLISH-TERRAIN-COMPOSITE. The `compute_hillshade` atomic tool is the MUST-HAVE; the composite is the nice-to-have for the case-UX demo flow.

#### Part 3 — Tests

- Unit test `compute_hillshade` with a synthetic small DEM (e.g., 32×32 with known gradient) — verify each style preset runs cleanly
- Cache-hit test: call twice with identical args, second call hits cache (verify via mocked cache hit)
- Cache-miss-then-hit live integration: invoke with `force_refresh=True` on a small real DEM; then again without — second is cache hit

#### Part 4 — Register in tool registry

- Add `import` in `services/agent/src/grace2_agent/tools/__init__.py`
- Add eager import in `services/agent/src/grace2_agent/main.py` to ensure registration at startup
- Verify `--startup-only` shows the new tool(s) in the registry list

#### Part 5 — Live verification

- Run `.venv-agent/bin/python -m grace2_agent.main --startup-only` — confirm `compute_hillshade` (and `publish_terrain_composite` if implemented) in the tool list
- Run a live `compute_hillshade(dem_uri=<job-0075's cached DEM URI>, style="swiss_double")` → confirm produces a real GeoTIFF in cache + returns LayerURI

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/compute_hillshade.py` (NEW)
- `services/agent/src/grace2_agent/tools/publish_terrain_composite.py` (NEW; OK to defer to follow-up)
- `services/agent/src/grace2_agent/tools/__init__.py` — additive registration
- `services/agent/src/grace2_agent/main.py` — eager import addition only
- `services/agent/tests/test_compute_hillshade.py` (NEW)
- `services/agent/tests/test_publish_terrain_composite.py` (NEW; if implemented)
- `styles/hillshade_*.qml` (NEW; only if visible style needed) — optional
- `reports/inflight/job-0079-engine-20260608/`

### FROZEN

- All other tools/* files (especially `publish_layer.py` which job-0076 just touched)
- All workflows/* (sfincs_builder.py, model_flood_scenario.py, postprocess_flood.py)
- `services/workers/pyqgis/**` — no worker changes here
- `packages/contracts/**`, `web/**`, `infra/**`, `docs/srs/**`, `reports/complete/**`

### Acceptance criteria

- [ ] `compute_hillshade` atomic tool registered + visible at `--startup-only`
- [ ] 5 style presets work (unit tests against synthetic DEM)
- [ ] Cache integration verified (read_through + cache hit on repeat call)
- [ ] Live verification: invocation on a real DEM produces real hillshade GeoTIFF in GCS cache
- [ ] Existing tool registry stays consistent (no regression on the 17+ existing tools at startup)
- [ ] Single commit; no FROZEN edits
- [ ] If `publish_terrain_composite` deferred: explicit OQ-79-PUBLISH-TERRAIN-COMPOSITE noted with proposed shape
