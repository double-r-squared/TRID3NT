# Report: `compute_slope` atomic tool (DEM slope raster)

**Job ID:** job-0081-engine-20260608
**Sprint:** sprint-11 Stage 1 parallel
**Specialist:** engine
**Task:** NEW `tools/compute_slope.py` atomic tool wrapping `gdaldem slope`. Parameters: `output_unit: Literal["degrees", "percent"] = "degrees"`, `algorithm: Literal["Horn", "ZevenbergenThorne"] = "Horn"`. Cache key on (dem_uri, output_unit, algorithm). FR-DC integration (cacheable=True, ttl_class="static-30d", source_class="slope"). Returns LayerURI.
**Status:** ready-for-audit

## Summary

Implemented `compute_slope` atomic tool wrapping `gdaldem slope` with full FR-DC cache integration, FR-TA-3-complete docstring, and 10 unit tests covering degrees/percent output, Horn/ZevenbergenThorne algorithms, cache miss/hit, LayerURI field correctness, and typed error paths. Live verification against the Fort Myers DEM (job-0075) produced a real slope GeoTIFF at `gs://grace-2-hazard-prod-cache/cache/static-30d/slope/0d2775ff027270e94758f2b90519caa2.tif` (63,832 bytes) and confirmed cache hit on the second call. Full agent test suite passes: 212 passed, 5 skipped, 0 failed.

## Changes Made

- **File:** `services/agent/src/grace2_agent/tools/compute_slope.py` (NEW)
  - `@register_tool` with `AtomicToolMetadata(name="compute_slope", ttl_class="static-30d", source_class="slope", cacheable=True)`.
  - GDAL command: `["gdaldem", "slope", input, output, *flags]` — `-p` for percent, `-alg ZevenbergenThorne` for the alternate algorithm.
  - `_get_gdaldem_bin()`: resolves via `GRACE2_GDALDEM_BIN` env var → `shutil.which("gdaldem")` → grace2 conda-env fallback.
  - `_download_dem_bytes()`: handles `gs://` and local file paths; maps failures to `SlopeComputeError(DEM_DOWNLOAD_FAILED)`.
  - `_run_gdaldem_slope()`: `subprocess.run` with 300 s timeout; maps failures to `SlopeComputeError(GDALDEM_FAILED)`.
  - Cache key on `(dem_uri, output_unit, algorithm)` via `read_through(storage_client=_storage_client)`.
  - FR-TA-3 docstring with LLM guidance: prefer "degrees" default; pick "percent" for road grade/engineering; pick "ZevenbergenThorne" for rough terrain.
  - `style_preset="continuous_dem"` placeholder (slope-specific QML preset deferred — OQ-81-SLOPE-STYLE-PRESET).

- **File:** `services/agent/src/grace2_agent/tools/__init__.py` — added 1 line (job-0081 eager import)

- **File:** `services/agent/src/grace2_agent/main.py` — added 1 line in `_import_tools_registry()` (job-0081 eager import)

- **File:** `services/agent/tests/test_compute_slope.py` (NEW, 10 tests)

## Decisions Made

- **`style_preset="continuous_dem"` placeholder.**
  - Rationale: no slope QML preset in v0.1 styles/. Nearest existing preset used. Deferred to follow-up.

- **`role="context"` for slope output.**
  - Rationale: slope is terrain context, not a primary hazard output. Matches engine.md framing.

- **Private `_storage_client` / `_bucket` DI kwargs.**
  - Rationale: mirrors test-isolation pattern from `test_data_fetch.py`. Passed directly to `read_through(storage_client=...)` so the cache shim never needs a live GCS client in tests.

## Invariants Touched

- **Determinism boundary (1): preserves** — typed LayerURI returned; no prose metrics.
- **Deterministic workflows (2): preserves** — zero LLM calls.
- **Engine registration, not modification (3): preserves** — new file only.
- **Minimal parameter surface (10): preserves** — only `dem_uri`, `output_unit`, `algorithm`.

## Open Questions

- **OQ-81-SLOPE-STYLE-PRESET:** `style_preset="continuous_dem"` is a placeholder. A `slope_degrees.qml` / `slope_percent.qml` preset should be authored when the QML preset library expands (FR-QS-5 covers 7 presets; slope is not in v0.1). TENTATIVE: deferred to follow-up sprint.

## Dependencies and Impacts

- **Depends on:** job-0033 (fetch_dem pattern), job-0039 (cache shim, read_through contract)
- **Affects:** Future `compute_terrain_derivatives` composite tool.

## Verification

**Tests run:** 10/10 pass in `test_compute_slope.py`; 212/212 pass in full agent suite (5 skipped).

- Synthetic gradient tests (gdaldem real binary): 1° DEM → 1.0° degrees (±0.1°); percent ≈ 1.745% (tan(1°)×100); both algorithms produce correct output on 5° synthetic DEM.
- Cache miss/hit cycle verified via mocked GCS.
- All 4 (unit, algorithm) combinations produce distinct cache keys.

**Live E2E evidence:**

```
$ GRACE2_GDALDEM_BIN=/home/nate/miniforge3/envs/grace2/bin/gdaldem \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  .venv-agent/bin/python -c "
from grace2_agent.tools.compute_slope import compute_slope
result = compute_slope(
    dem_uri='gs://grace-2-hazard-prod-cache/cache/static-30d/dem/8aa23925b1df7a56e6f9a6afac210ab2.tif',
    output_unit='degrees',
)
print(result)
"

INFO grace2_agent.tools.compute_slope compute_slope: running gdaldem slope
    cmd=.../gdaldem slope /tmp/tmpt0j8r9yq.tif /tmp/tmptricguct.tif
INFO grace2_agent.tools.cache read_through miss-write tool=compute_slope
    key=0d2775ff027270e94758f2b90519caa2 bytes=63832
    customTime=2026-06-08T07:59:51.408257+00:00

layer_id:    slope-8aa23925b1df7a56e6f9a6afac210ab2-degrees-Horn
layer_type:  raster
uri:         gs://grace-2-hazard-prod-cache/cache/static-30d/slope/0d2775ff027270e94758f2b90519caa2.tif
role:        context
units:       degrees

# Second call — cache hit:
INFO grace2_agent.tools.cache read_through hit tool=compute_slope
    key=0d2775ff027270e94758f2b90519caa2 bytes=63832
```

```
$ GRACE2_SKIP_WORKER_SUBMITTER=1 .venv-agent/bin/python -m grace2_agent.main --startup-only
INFO grace2_agent.main tool registry loaded: 21 tool(s): [..., 'compute_slope', ...]
INFO grace2_agent.main --startup-only: tool registry verified; exiting without serving
```

**Results:** pass
