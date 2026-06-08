# Audit: `compute_zonal_statistics` atomic tool (hazard-analysis primitive)

**Job ID:** job-0083-engine-20260608, **Sprint:** sprint-11 Stage 1 parallel, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:** all in-flight 0079/0080/0081/0082 — same tool-registration pattern though this tool returns a dict (not a LayerURI).

**SRS references:** FR-TA-2, FR-CE-8, FR-DC.

**Required reads:**
- `services/agent/src/grace2_agent/tools/fetch_dem.py` — pattern reference (cache + GCS download + result return)
- `services/agent/src/grace2_agent/tools/cache.py` — read_through cache contract
- `packages/contracts/src/grace2_contracts/` — find what return type is appropriate (likely a typed dict; not a LayerURI since this is analysis output, not a layer)

### Why this job exists

The foundational hazard-analysis primitive. Every future case the user runs will eventually ask "how much of X is in Y" — population in flood zone, building footprint in fire perimeter, road length in storm surge, asset value in earthquake intensity zone. Today no tool can answer that directly. This is it.

### Scope

NEW file: `services/agent/src/grace2_agent/tools/compute_zonal_statistics.py`

```python
@register_tool(
    cacheable=True,
    ttl_class="dynamic-1h",  # depends on inputs; safer not-too-long
    source_class="zonal_statistics",
)
def compute_zonal_statistics(
    value_raster_uri: str,
    zone_input_uri: str,
    statistics: list[Literal["count", "sum", "mean", "min", "max", "std", "median", "percentile_25", "percentile_75", "percentile_95"]] = ["count", "sum", "mean", "max"],
    zone_threshold: float | None = None,
    nodata_value: float | None = None,
) -> dict[str, Any]:
    """Compute zonal statistics — aggregate values from a raster within zones.

    Common uses:
        - Population in flood zone: value=population_raster, zone=flood_depth_raster, zone_threshold=0.5 (≥0.5m)
        - Mean elevation in watershed: value=DEM, zone=watershed_polygon
        - Building footprint area exposed: value=buildings_raster, zone=hazard_raster
        - Max wind in damage assessment area: value=wind_raster, zone=admin_boundary

    Parameters:
        value_raster_uri: the raster whose values are aggregated.
        zone_input_uri: either a raster (mask: non-zero = in zone) OR
            a vector (FlatGeobuf/GeoJSON polygons; each polygon = one zone).
            Auto-detect from MIME / file extension.
        statistics: list of summary stats to compute.
        zone_threshold: if zone_input is a raster, treat values ≥ this as
            "in zone". Useful for flood-depth thresholds (e.g., 0.5m).
            If None, non-zero = in zone.
        nodata_value: explicit nodata for the value raster. Defaults to
            reading from the raster's nodata metadata.

    Returns:
        dict with structure:
            {
              "by_zone": {<zone_id>: {<stat>: value, ...}, ...},  # per-zone if vector input
              "aggregate": {<stat>: value, ...},                  # whole-area aggregate
              "value_raster": ...,                                # provenance
              "zone_input": ...,
              "computed_at": ISO timestamp,
              "units": ...,                                       # propagated from value raster
            }

    LLM guidance:
        - Use this whenever the user asks "how much" / "how many" / "what's
          the average" of one quantity within a zone defined by another.
        - For hazard exposure: value=hazard intensity, zone=admin/asset/exposure layer.
        - For impact: value=population/buildings/assets, zone=hazard threshold.
    """
```

**Implementation:**
- Use `rasterstats` library if available (cleanest) OR roll your own with `rasterio` + `numpy.where()` + vector reading via `fiona`/`geopandas` for vector zones.
- Check `.venv-agent` for `rasterstats` first; if not present, decide between adding to pyproject.toml (small additive — surface as OQ-83 if material) or rolling your own. Rolling is fine; the libraries are simple.
- Auto-detect zone input type via `rasterio.open(...).meta` → if ok, raster; else try `fiona.open(...)` → vector. Or by file extension (`.tif/.tiff` raster; `.fgb/.geojson/.gpkg` vector).
- Cache key derivation: SHA256 of `(value_raster_uri, zone_input_uri, sorted(statistics), zone_threshold, nodata_value)`.

**Tests:**
- Synthetic case 1: value=ramp raster 1-100, zone=mask covering top-right quadrant → mean ≈ 75, sum = sum of values in that quadrant
- Synthetic case 2: value=population raster (synthetic), zone=flood depth raster with threshold 0.5m → population exposure scalar
- Synthetic case 3: vector zone input (single rectangle polygon) → aggregate stats over that polygon
- Cache hit on repeat call

**Register in registry:** `tools/__init__.py` + `main.py` (1 line each). Confirm via `--startup-only`.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/compute_zonal_statistics.py` (NEW)
- `services/agent/src/grace2_agent/tools/__init__.py` — 1 line
- `services/agent/src/grace2_agent/main.py` — 1 line
- `services/agent/tests/test_compute_zonal_statistics.py` (NEW)
- `reports/inflight/job-0083-engine-20260608/`

### FROZEN

All other tools/* (including concurrent compute_hillshade / compute_colored_relief / compute_slope / compute_aspect from 0079-0082); all workflows/, services/workers/, packages/contracts/, web/, infra/, docs/srs/, styles/. `reports/complete/**`. `services/agent/pyproject.toml` — only edit if you need to add `rasterstats`; if so, justify in the report.

### Concurrency note

5 concurrent engine jobs (0079/0080/0081/0082/0083) each add 1 line to `main.py` + `tools/__init__.py`. If git surfaces conflict, append + orchestrator reconciles.

### Acceptance criteria

- [ ] `compute_zonal_statistics` registered + visible at `--startup-only`
- [ ] Raster-zone input + vector-zone input both work
- [ ] All requested statistics compute correctly (verified on synthetic data with known expected values)
- [ ] Cache integration verified
- [ ] Live verification: run against job-0075's flood COG (value) + a known mask (zone) — produce a real exposure number
- [ ] No FROZEN edits beyond pyproject.toml (if needed for rasterstats; justify)
- [ ] Single commit
