# OQ-4: HydroMT Integration Depth for SFINCS Setup

**Decision date:** 2026-06-06
**Decided by:** engine specialist (job-0038-engine-20260606), confirmed by orchestrator audit
**Status:** DECIDED — resolves SRS §6 OQ-4
**Implements:** `build_sfincs_model` tool in `services/agent/src/grace2_agent/tools/model_setup.py` (job-0042)

---

## 1. Context

SRS §6 OQ-4 reads:

> **HydroMT integration depth**: full reliance for SFINCS setup, or custom config builders? HydroMT is powerful but adds a heavy dependency.

The SRS §2.3 engine catalog lists SFINCS as "Python shim via HydroMT" for v0.1, establishing HydroMT as part of the integration mode but leaving the coupling depth unresolved. The v0.1 milestone (M5 demo) targets Hurricane Ian / Fort Myers, ≤200 km² at 30 m resolution, with an end-to-end wall-clock budget of ≤15 minutes (NFR-P-4). The `build_sfincs_model` tool wraps whatever depth is chosen here; its signature is fixed by Decision K (minimal parameter surface — location + forcing only; DEM, Manning's, boundary conditions are resolved internally).

Three depths were considered. They span the spectrum from "HydroMT does almost everything" to "we write every line."

---

## 2. Options Considered

### Option A — Full HydroMT (`hydromt-sfincs` plugin end-to-end)

HydroMT-SFINCS is a Deltares-maintained plugin (GPLv3, Python ≥ 3.11) that automates the complete SFINCS setup pipeline from raw data to a ready-to-run `sfincs.inp`. The build is driven by a YAML configuration file referencing named datasets in a HydroMT data catalog; the Python API exposes this as a sequence of component `create()` calls (elevation, roughness, forcing, boundary conditions, mask, subgrid tables) that HydroMT resolves against the catalog.

**Dependency footprint (runtime):** 16 direct packages beyond HydroMT core. Core HydroMT itself pulls in: xarray, rasterio, rioxarray, geopandas, shapely, pyproj, pyflwdir, dask, numba, bottleneck, scipy, netcdf4, zarr, pooch, fsspec, pydantic, click, pyyaml. Plugin-specific additions: xugrid (≥ 0.15.1 < 1.0), pyflwdir (≥ 0.5.10), mapbox\_earcut, cht\_tide, numba (also in core), pillow, affine. Total container weight is substantial — numba JIT compilation, GDAL via rasterio, dask, and xugrid make the install ≈ 800 MB–1 GB in a slim image.

**Code surface in our repo:** `build_sfincs_model` becomes a thin orchestrator (≈ 100–150 lines): construct a `DataCatalog` pointing at our atomic-tool LayerURIs (fetched to GCS), construct `SfincsModel`, call `model.grid.create(...)`, `model.elevation.create(...)`, `model.roughness.create(...)`, `model.forcing.meteo.create(...)`, `model.forcing.water_level.create(...)`, then `model.write()`. The catalog bridging layer (mapping our `LayerURI` GCS paths to HydroMT catalog entries) is ≈ 50 lines.

**Generalization to other Deltares solvers:** HydroMT has separate plugins for wflow (`hydromt-wflow`) and Delft3D FM (`hydromt-delft3dfm`). Adopting full HydroMT establishes the catalog-bridging pattern once; adding wflow or Delft3D FM is a new plugin, not a new architecture. The shared `DataCatalog` API, `@hydromt_step` decorator, and YAML build-config convention are the same across all plugins.

**Determinism:** The build is driven by a frozen YAML config + named catalog entries. Given the same config and the same input rasters (GCS-cached), the output `sfincs.inp` is deterministic. Invariant 2 (deterministic workflows) is preserved.

**Failure mode — upstream data surprise:** HydroMT's roughness component maps landcover class integers to Manning's values via a user-supplied CSV mapping table. If NLCD changes class encoding (historically a real risk), HydroMT silently fills unmatched classes with the `manning_land`/`manning_sea` defaults (it logs a warning but does not raise). This is a silent-wrong-answer failure: the model runs but uses incorrect roughness in cells where the mapping failed. Detection requires validating the mapping table against the NLCD vintage before calling `build_sfincs_model`. The elevation component handles unexpected nodata via IDW interpolation, logging a warning — similar silent-degradation mode.

**Progress reporting / PipelineEmitter:** HydroMT's `@hydromt_step` decorator tracks steps internally but does not emit progress signals through any external callback. The full-HydroMT path runs as a single synchronous call inside the solver container; `update_progress` (job-0035 `PipelineEmitter`) is not callable from inside a Cloud Run Job without passing a channel reference through. Setup progress is not observable step-by-step; only coarse-grained start/finish is visible at the Cloud Workflows orchestration layer (FR-CE-2). This is acceptable for v0.1 — model setup takes 1–3 minutes of the 15-minute budget; the solver run dominates and will emit progress via Cloud Workflows callbacks.

**Cancellation:** A cancel signal arrives via Cloud Workflows `terminate` (invariant 8, NFR-R-3). Because the HydroMT setup runs inside the same Cloud Run Job as the solver dispatch, the Workflows `terminate` call signals the job to stop. The 30-second cancellation budget is met as long as the Cloud Run Job does not trap `SIGTERM`.

**Licensing:** GPLv3. HydroMT-SFINCS's GPLv3 license applies to the plugin itself; it does not contaminate our MIT/Apache-licensed agent code unless we fork or statically link it. Running it in a separate container (Cloud Run Job) with clean process boundaries keeps our license posture clean. This must be documented in the dependency manifest.

---

### Option B — Partial HydroMT (preprocessing only; handcrafted `sfincs.inp`)

Use `hydromt-sfincs` only for the messy geospatial steps — DEM hydro-conditioning (burn-in of rivers, fill sinks), the Manning's reclassification raster, and possibly the active-cell mask. Write `sfincs.inp` manually from our atomic-tool outputs. Boundary condition time series (water levels, wind, pressure, rainfall) are written directly from our `fetch_*` tool outputs without going through HydroMT's forcing components.

**Code surface:** ≈ 400–600 lines across `build_sfincs_model` and helper modules — HydroMT invocations for the DEM and roughness steps, plus hand-authored SFINCS input file writers for the config, boundary conditions, and forcing.

**Dependency footprint:** Same as Option A for the DEM/roughness preprocessing; we cannot shed the heavy dep stack if we use any part of `hydromt-sfincs`. The coupling to HydroMT's catalog API remains necessary for the preprocessing steps.

**Failure mode:** Similar to Option A for the HydroMT-driven steps. The hand-authored `sfincs.inp` section is fully under our control and fails loudly (Python exceptions) rather than silently, which is better for observability.

**Cancellation / progress:** Same as Option A for the preprocessing phase. The hand-authored forcing-write phase is pure Python and can checkpoint between writes.

**Verdict:** Captures most of the complexity of Option A without a meaningful reduction in dependency surface. The only gain is explicit control over the `sfincs.inp` format, which is valuable for future engine-specific tuning but adds ≈ 300 lines of hand-maintained SFINCS input format knowledge. The tradeoff does not favor this option for v0.1.

---

### Option C — Custom config builders (skip HydroMT entirely)

Write Python that consumes our `LayerURI` outputs from `fetch_dem`, `fetch_landcover`, `fetch_hurricane_track`, `fetch_tide_gauge` and emits `sfincs.inp`, the DEM binary (`.dep`), the Manning's grid (`.man`), and the boundary forcing files directly. No HydroMT dependency.

**Code surface:** ≈ 1 500–2 500 lines: a DEM hydro-conditioning pipeline (sink-fill, river burning, grid resampling to SFINCS resolution), a Manning's reclassification pipeline, boundary condition time series formatters, an `sfincs.inp` writer, and an active-cell mask generator.

**Dependency footprint:** Much lighter — rasterio + numpy + scipy + pandas + pyproj is sufficient. The container is ≈ 300–400 MB vs ≈ 900 MB+ for HydroMT.

**Generalization:** Custom builders are SFINCS-specific by construction. Adding wflow or Delft3D FM means writing 1 500–2 500 more lines of solver-specific setup code per solver. No reuse.

**Determinism:** Fully deterministic by construction — pure numpy/rasterio operations.

**Failure mode — upstream data surprise:** Explicit and loud. A class integer not in our mapping CSV raises a `KeyError`; an unexpected CRS raises in pyproj. No silent fallbacks, which is the strength of this option. The weakness is that "explicit and loud" also means more failure paths to test and handle.

**Progress reporting:** Pure Python — `update_progress` can be called between pipeline stages, enabling step-by-step progress reporting through PipelineEmitter's opt-in `update_progress(step_id, ...)` path (job-0035). This is the best option for progress granularity.

**Cancellation:** Same Cloud Workflows `terminate` path. Python code between numpy calls is interruptible; we can add cooperative checkpoint hooks.

**Verdict:** Maximum control but disproportionate custom-code surface for v0.1. The ≈ 1 500–2 500 lines of DEM conditioning and SFINCS format logic duplicate Deltares engineering that is already tested at scale by `hydromt-sfincs`. This option defers to post-v0.1 only if HydroMT's GPLv3 licensing or dependency weight becomes a blocking constraint.

---

## 3. Decision

**Selected: Option A — Full HydroMT (`hydromt-sfincs` end-to-end).**

Rationale against the three filters from the kickoff:

1. **Decision J tractability (easy wins first):** HydroMT-SFINCS is the Deltares-recommended integration path for SFINCS, actively maintained, peer-reviewed (Eilander et al. 2023, *NHESS*), and proven at global scale. The setup pipeline (DEM conditioning, Manning's reclassification, boundary condition ingestion) is exactly what HydroMT was designed for. Writing equivalent Python from scratch (Option C) is a multi-sprint investment that Decision J explicitly defers when a tractable alternative exists.

2. **NFR-P-4 (≤15 min, ≤200 km², 30 m):** HydroMT setup for a 200 km² domain at 30 m is measured in minutes (2–4 min) in published benchmarks; the SFINCS solver run for a storm surge event at this scale is 5–10 min on a `medium` Cloud Run Job (4 vCPU / 8 GB). The full pipeline fits within 15 minutes. Option C would not change solver runtime, and setup time is not a bottleneck at this scale.

3. **Invariant 2 (deterministic workflows):** Full HydroMT satisfies determinism — given the same YAML build config and the same GCS-cached input rasters, the output `sfincs.inp` is byte-for-byte reproducible. The data catalog bridging layer (mapping our `LayerURI` paths to HydroMT catalog entries) must be written and frozen at `build_sfincs_model` call time; no randomness is introduced.

**The decisive tradeoff:** Option A adds ≈ 900 MB to the solver container image and pulls in GPLv3 code, but eliminates ≈ 1 500 lines of custom format logic that would otherwise require independent maintenance and test coverage. For v0.1, eliminating the custom-code surface is the correct tractability call.

---

## 4. Consequences

### Immediate (job-0042: `model_flood_scenario` workflow)

- `build_sfincs_model(dem_uri, landcover_uri, forcing, bbox, options) → ModelSetup` wraps `SfincsModel` with a `DataCatalog` constructed from our atomic-tool `LayerURI` GCS paths.
- The YAML build config (grid resolution, coordinate reference system, bbox) is generated programmatically inside `build_sfincs_model`; it is not a user input.
- A Manning's mapping CSV (NLCD integer → Manning's value) must be authored and version-pinned in the repo. When NLCD updates its class encoding, this file is updated. **Validation gate required:** before calling HydroMT's roughness component, `build_sfincs_model` must verify that the fetched NLCD vintage's class set is a subset of the mapping CSV; raise `SFINCSSetupError("LULC_MAPPING_MISMATCH")` on mismatch rather than allowing silent fallback.
- The solver container Dockerfile must include `hydromt-sfincs` and its dependencies. Container build is job-0040 (infra). Licensing: GPLv3 applies to the plugin inside the container; document in `infra/THIRD_PARTY_LICENSES.md`.

### Immediate (job-0039: data fetcher tools)

- `fetch_dem` must return a `LayerURI` whose `uri` is a GCS path readable by the solver container's service account. HydroMT's `raster` driver supports GCS via `fsspec[gcs]` — the catalog entry `filesystem: gcs` must be set in the bridging layer.
- `fetch_landcover` must return the NLCD vintage year alongside the `LayerURI` so `build_sfincs_model` can validate the mapping CSV covers that vintage.
- `fetch_hurricane_track`, `fetch_tide_gauge`, and `fetch_storm_rainfall` URIs are consumed directly by HydroMT's forcing components (NetCDF or CSV format). Forcing tools must write outputs in a format HydroMT's `meteo` and `water_level` components accept — confirm format at job-0039 time.

### Downstream (wflow / Delft3D FM generalization, post-v0.1)

- Adding `hydromt-wflow` or `hydromt-delft3dfm` reuses the same data catalog bridging pattern established here. The architectural investment is not SFINCS-specific.
- Each new solver plugin will need its own container and its own mapping/validation layer; the pattern is reusable.

### Technical debt acknowledged

- HydroMT's silent-fallback behavior for unmatched landcover classes requires an active validation gate in `build_sfincs_model`. Without this gate, a changed NLCD encoding produces a silently wrong Manning's grid — a violation of Invariant 7 (no silent wrong answers).
- Container image size (≈ 900 MB+) will affect cold-start time on Cloud Run Jobs. Measure at M5; if cold-start exceeds 2–3 min, consider a pre-warmed or pinned image tag.
- HydroMT v2.0 is currently in release-candidate status (v2.0.0-rc2 as of November 2025); v1.x is the stable API. Pin `hydromt-sfincs >= 1.1.2, < 2.0` until v2.0 exits RC. The v2.0 API has breaking changes in `SfincsModel` (component-based architecture replaces monolithic `setup_*` methods). When upgrading, refer to the published migration guide.

---

## 5. Open Questions (TENTATIVE — require orchestrator triage)

**OQ-4a (scope of HydroMT generalization — TENTATIVE: all-Deltares-solvers):** This decision establishes HydroMT as the integration layer for SFINCS. Should the data catalog bridging pattern be designed now as a reusable `HydroMTCatalogBridge` class shared across future solver plugins, or deferred to when the second solver (wflow) is added? TENTATIVE: defer the generic class; note the pattern in docstrings so the second solver can extract it.

**OQ-4b (pin-or-vendor strategy — TENTATIVE: PyPI pin with `< 2.0`):** Should `hydromt-sfincs` be pinned to a tested release on PyPI (e.g., `hydromt-sfincs >= 1.1.2, < 2.0`) or vendored as a git submodule to isolate from upstream changes? TENTATIVE: pin on PyPI at `>= 1.1.2, < 2.0`; re-evaluate when v2.0 exits RC. Vendoring is disproportionate overhead for v0.1 given HydroMT's active maintenance track record.

**OQ-4c (behavior when bbox is unsupported — TENTATIVE: raise typed error):** If HydroMT cannot set up a valid model for the user-supplied bbox (e.g., no DEM coverage, coastal bbox that extends beyond tide gauge range, or a CRS that HydroMT cannot project), should `build_sfincs_model` raise a typed `SFINCSSetupError` with a structured message, or attempt a partial setup and return a degraded `ModelSetup`? TENTATIVE: raise `SFINCSSetupError` with an error code (`DEM_COVERAGE_GAP`, `FORCING_OUT_OF_RANGE`, `LULC_MAPPING_MISMATCH`) so the pipeline strip surfaces a meaningful failure rather than dispatching a broken model to the solver.

---

## 6. References

- SRS §6 OQ-4 (original question text)
- SRS §2.3 Engine catalog, v0.1 SFINCS row ("Python shim via HydroMT") and Decision J (tractability principle)
- SRS FR-CE-1/2/3 (solver containerization, Cloud Workflows orchestration, compute classes)
- SRS NFR-P-4 (≤15 min end-to-end for ≤200 km², 30 m)
- SRS FR-TA-2 `build_sfincs_model` tool specification
- Eilander et al. (2023), "A globally applicable framework for compound flood hazard modeling," *Nat. Hazards Earth Syst. Sci.*, 23, 823–846. [https://doi.org/10.5194/nhess-23-823-2023](https://doi.org/10.5194/nhess-23-823-2023) — peer-reviewed application of HydroMT-SFINCS at global scale.
- HydroMT-SFINCS GitHub: [https://github.com/Deltares/hydromt_sfincs](https://github.com/Deltares/hydromt_sfincs) — `pyproject.toml` dependency list inspected 2026-06-06.
- HydroMT core GitHub: [https://github.com/Deltares/hydromt](https://github.com/Deltares/hydromt) — `pyproject.toml` dependency list inspected 2026-06-06.
- HydroMT data catalog docs (v0.9.4): GCS support is experimental for `raster` driver; `fsspec[gcs]` required.
- `hydromt-sfincs` changelog (v1.1.0, v1.2.0, v2.0.0-rc1): API stability notes, subgrid file format change (binary → NetCDF in v1.1.0), NumPy compatibility issue (v1.2.0), breaking `SfincsModel` API in v2.0.0-rc1.
- job-0035-agent-20260606: `PipelineEmitter.update_progress` is M5+ solver opt-in; not required for model setup phase.
- job-0040-infra-20260606: SFINCS solver container (owns Dockerfile; this decision informs the required apt/conda layers).
