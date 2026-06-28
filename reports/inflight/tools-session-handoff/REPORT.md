# TOOLS SESSION -> ORCHESTRATOR HANDOFF (2026-06-27)

**From:** the Tools / Agent-Config specialist session (own worktree, branch `tools-work`).
**To:** the Development Orchestrator (integration + deploy + cross-seam wiring).
**Route:** this file is `reports/inflight/tools-session-handoff/REPORT.md` on branch `tools-work`.

---

## TL;DR
This session added **35 new tools + 3 upgrades** (registry **129 -> 164**), proved a **coalesced QGIS Processing worker**, and produced **2 worker escalations**. Everything is additive on branch `tools-work` (pushed). To integrate: **(1) merge `tools-work` -> main, (2) deploy the agent, (3) wire the 5 seams in section 3** (none blocking — the tools degrade gracefully without them). Full agent suite is green except 3 pre-existing `swmm-api` failures that are NOT from this work.

---

## 1. The branch (how to integrate)
- **Branch:** `tools-work` (origin), HEAD `d36e508` — **merge the branch HEAD; treat the `PROJECT_LOG.md` tail as the authoritative landed-tool list**.
- **Self-contained + additive:** new files under `services/agent/src/grace2_agent/tools/` + tests under `services/agent/tests/`, plus the 3 registration files (`tools/__init__.py`, `categories.py`, `data/tool_query_corpus.yaml`) and the `test_tools_registry.py` global-query audit. No edits to `server.py`, `adapter.py`, contracts, `services/workers/`, web, or infra (those are your seam — see section 3).
- **Deploy:** merge -> main, then deploy the agent via the custom `grace2-runshell` SSM doc (standing auth). Registry grows 129 -> 164.

---

## 2. Tools added (authoritative list = PROJECT_LOG tail; summary here)
All built **local-first vs the REAL source** + **real S3 read_through round-trips** + per-tool tests by parallel agent workflows.

- **a+b+c** (`2404603`): `digitize_water_body` (Sentinel-2 NDWI water polygons), `fetch_usgs_earthquakes`, `fetch_hifld_critical_infrastructure`, `fetch_cdc_svi`, `fetch_sentinel2_truecolor`, `compute_home_range_kde`, `compute_movement_trajectory`; **UPGRADE** `fetch_usace_dams` (authoritative NID behind the SSM secret-loader, ESRI mirror fallback, hazard/state filters activated).
- **fetchers2** (`abcc408`): `fetch_epa_frs_facilities`, `fetch_us_drought_monitor`, `fetch_overpass_pois`, `fetch_census_acs`, `fetch_landsat_imagery`, `fetch_noaa_sst`, `fetch_openfema_disasters`, `fetch_esri_landcover_10m`.
- **batch3+4** (`a82e021`, `c93bdfa`): `fetch_usgs_volcano_alerts`, `fetch_usgs_water_quality`, `fetch_usgs_groundwater_levels`, `fetch_snotel_snow`, `fetch_sentinel1_sar`, `fetch_modis_lst`, `fetch_hifld_transmission_lines`, `fetch_lehd_jobs`, `fetch_nws_river_forecast`, `fetch_copernicus_dem`, `fetch_chirps_precipitation`, `fetch_ghsl_population`, `fetch_jrc_global_surface_water`, `fetch_soilgrids`.
- **batch5** (LANDED): EXTEND `fetch_storm_events_db` (+bbox/date filters), EXTEND `fetch_river_geometry` (+waterway_type); new `fetch_epa_ejscreen`, `fetch_tsunami_events`, `fetch_climate_normals`, `fetch_noaa_coops_currents`, `fetch_airnow_air_quality` (secret), `fetch_openaq_measurements` (secret). **Check the PROJECT_LOG tail for the final landed/blocked status.**

**Demo tie-ins worth noting:** `fetch_usgs_water_quality` + `fetch_usgs_groundwater_levels` + `fetch_epa_frs_facilities` ground-truth + add exposure to the MODFLOW-GWT contamination-plume demo; `fetch_sentinel1_sar` + `fetch_jrc_global_surface_water` + `digitize_water_body` form a flood/water-mapping spine; `fetch_copernicus_dem` + `fetch_ghsl_population` + `fetch_soilgrids` + `fetch_chirps_precipitation` give global (non-CONUS) coverage.

---

## 3. Seams to wire (YOUR integration work — services/workers + server.py + web + infra)

### 3a. QGIS coalesced Processing worker -> Fargate Spot (REFRAMES job-0308)
- **Dir:** `reports/inflight/qgis-coalesced-worker/` (`processing_server.py` reference + `HANDOFF.md` spec + `coalescing_proof.py` / `server_proof.py`).
- **Proven (local, real DEM):** one warm `QgsApplication` serves N `processing.run` requests with one init (~0.7-3.3s) -> **~77% of a multi-turn's QGIS wall-time saved** vs `docker run qgis_process`-per-call; 379 algos incl GRASS; server proof = 4 s3-in/out requests served by INIT_COUNT=1.
- **TODO:** place `processing_server.py` under `services/workers/qgis/`; build slim image (existing `services/workers/qgis/Dockerfile`) + **fix the grass binary path** (grass:* showed n/a locally); **Fargate SPOT** task + IAM (s3 read cache/cog/fgb, write runs); lifecycle = get-or-create + pre-warm-on-plan + Spot-reclaim-retry + **turn-tail idle teardown** (the server's idle-watchdog); bind the agent-side **HTTP submitter** (`set_worker_submitter`, the `_WORKER_SUBMITTER` seam in `passthroughs.py`). **Turn-scoped warm Spot, NOT always-on.** See `HANDOFF.md`.

### 3b. deepforest CPU Batch worker (escalation)
- **Dir:** `reports/inflight/deepforest-worker-escalation/` (`run_deepforest_tree_crown.py` proven inert scaffolding + test + `ESCALATION.md` with the frozen worker contract).
- **Why escalated:** canopy doesn't run its model inline either -- it dispatches to a `services/workers/canopy` Batch worker; DeepForest (PyTorch RetinaNet) is the same shape, so its inference is `services/workers/` (your seam). The tool is HELD (unregistered) so it never dead-ends in the live registry.
- **TODO:** provision `services/workers/deepforest/` mirroring `services/workers/canopy/` (entrypoint + Dockerfile + the build_spec contract in ESCALATION.md), add the `deepforest` solver-registry entry + `GRACE2_AWS_BATCH_JOB_DEF_DEEPFOREST`, then register the held tool (import_line + categories + corpus are in ESCALATION.md).

### 3c. server.py heavy-sync offload
- Add **`digitize_water_body`** to `_ALWAYS_OFFLOAD_SYNC_TOOLS` in `services/agent/src/grace2_agent/server.py` (heavy sync raster+vector like `compute_ndvi`; the building agent flagged it) so it offloads via `asyncio.to_thread` and never stalls the WS heartbeat. Review the other new heavy raster builders for the same: `fetch_sentinel1_sar`, `fetch_modis_lst`, `fetch_landsat_imagery`, `fetch_copernicus_dem`, `fetch_chirps_precipitation`, `fetch_ghsl_population`, `fetch_jrc_global_surface_water`, `fetch_soilgrids`, `fetch_esri_landcover_10m`, `fetch_sentinel2_truecolor`.

### 3d. Web/render style presets (web/render specialist)
New `style_preset` tokens the new tools emit. **Tools fall back gracefully** (percentile/default render) until these are registered in the render seam (`_TITILER_STYLE_REGISTRY` / categorical-palette / QML + web legends):
- vector: `water_bodies`, `epa_frs_facilities`, `overpass_pois`, `volcano_alerts`, `nws_river_gauges`, `hifld_*` (transmission lines), `us_drought_monitor` (5-class D0-D4 tan->dark-red ramp), `ejscreen`, `tsunami`, `coops_currents`, `air_quality`.
- raster: `s2_truecolor` (RGB passthrough), `sar_backscatter_db` (grayscale dB), `land_surface_temp_c`, `sst_celsius`, `water_occurrence_pct` (blue ramp), `soil_property`, `precip_mm`, categorical for `fetch_esri_landcover_10m` (reuse the NLCD categorical family).
- Also: the slope/aspect colormaps from `227518e` (`slope_angle_deg` ylorrd, `aspect_compass_deg` cyclic hsv) still need the **frontend legends** (slope colorbar + aspect compass wheel) + confirm `hsv` exists in the box's rio-tiler.

### 3e. Optional future tool extensions (in-seam, can stay tools-session)
- batch5 already does: `fetch_storm_events_db` +bbox/date, `fetch_river_geometry` +waterway_type. No action needed beyond merge.

---

## 4. Verification
- **Full agent suite:** 9142 passed, 113 skipped, **3 failed** — all in `tests/test_granularity_gate.py`, caused by a **missing `swmm-api` dep, PRE-EXISTING and unrelated** to this work (confirmed present before any of these commits via stash-rerun). Note: run with `TMPDIR` on a non-tmpfs disk — the box's `/tmp` is a 7.8G tmpfs that other sessions fill, and a full tmpfs spuriously fails ~60 IO-bound tests. Everything this session added is green, including the registration/category/corpus-coverage gates + the `supports_global_query` audit.
- Per-tool: each tool has a local-first proof vs the real source + a real S3 round-trip (evidence captured in `PROJECT_LOG.md` and the workflow result records).

---

## 5. This session's commits on `tools-work`
```
a82e021 batch3+4 -- 14 keyless fetchers (144 -> 158)
c93bdfa fetch_usgs_volcano_alerts
abcc408 fetchers2 -- 8 keyless (136 -> 144)
2404603 a+b+c -- 7 new + USACE NID upgrade + deepforest escalation (-> 136)
06f49da coalesced QGIS Processing worker -- reference + handoff
227518e #3 slope/aspect colormaps (backend)
6f92359 / f0508ab / 72f8d9b  (earlier: colormap deferral, mongo cleanup + colormaps, SLR siblings)
```
(batch5 commit lands after this report — merge the branch HEAD.)

---

## 6. SRS note
Several new tools warrant FR/appendix mentions (the new data sources + the QGIS-coalescing architecture). Per the invariant, **specialists propose, only NATE lands SRS amendments** — flagging for a future amendment pass, not requesting one here.
