# Sprint-17 — Next-up North Stars + platform (roadmap manifest)

Status: PLANNED (NATE selected all four 2026-06-21). Grounded by roadmap-research workflow `wen0ew0mv` (real practitioner pipelines + GRACE-2 seams). Build NOT started; awaiting go.

Four tracks: **Compound flood** (lead demo), **Data-island #165** (platform gate), **MODFLOW river-seepage**, ~~HEC-RAS~~ -> **GeoClaw** (see Engine-backlog decision below).

## ENGINE-BACKLOG DECISION (NATE 2026-06-21, approved)

After the engine cloud/AI-drivability ranking ([[reference_engine_cloud_ai_drivability_ranking]]), NATE approved:
- **SWAP HEC-RAS -> GeoClaw.** HEC-RAS is XL/high-risk (Windows/GUI heritage, RASMapper mesh authoring, COM controller; headless-on-Linux unproven) and not on the drivability list. GeoClaw (Clawpack, BSD, pip, native-Python setrun.py/setplot.py, headless Linux, AMR, public data) solves the SAME 2D shallow-water core and covers dam-break / overland / surge PLUS tsunami (a new hazard class). Same depth-raster+animation deliverable, S-tier integration cost. HEC-RAS is SHELVED as a later, deliberately-scoped effort ONLY if FEMA-grade regulatory channel hydraulics with structures (bridges/culverts/levees, 1D gradually-varied profiles) become a hard requirement -- GeoClaw is not a drop-in for that.
- **ADD OpenQuake** (S-tier, AGPL, Docker+CLI+REST): probabilistic seismic hazard; pairs directly with the existing Pelicun impact path for an earthquake demo.
- **ADD Landlab** (S-tier, MIT, BMI): landslide / overland flow / landscape evolution -- a hazard class GRACE-2 lacks; snap-together components.
- **BMI BRIDGE (cross-cutting multiplier, do first among the new engines):** one CSDMS-BMI driver in the run_solver seam (initialize/update/finalize/get/set). Landlab + SFINCS + GeoClaw(PyClaw) are BMI-capable, so the bridge makes Landlab + GeoClaw near-free to plug in. Sequencing: BMI bridge -> Landlab + GeoClaw (via BMI) ; OpenQuake in parallel (containerized CLI, not BMI). This becomes its own sprint after sprint-17 Wave A/B land; the multi-hazard-workbench story = GeoClaw(tsunami/surge) + SFINCS(compound) + OpenQuake(seismic) + Landlab(landslide) + (later) ELMFIRE(wildfire) + HYSPLIT/FALL3D(dispersion).

## The gating contract (freeze before any engine track edits publish_layer)

All 3 engine demos + #165 touch `publish_layer.py` + the case snapshot. #165 is the only one that REWRITES the shared vector branch, so:

**J1 = #165 Phase 0 (durable-vector publish branch) LANDS + DEPLOYS FIRST, standalone.**

- Vector publish (`publish_layer.py` vector branch ~1693-1723) writes GeoJSON to `case-data/{case_id}/{layer_id}.geojson` in the **durable runs bucket** (NOT the TTL cache bucket) via existing `_read_vector_uri_as_geojson`/`_fgb_bytes_to_geojson` (pipeline_emitter.py:526/575), registers via `observe_published_layer` (data face = s3 uri, display face = asset url), returns a real URL; fail-open to noop. job-0308 QGIS-WMS composes on top, never replaces.
- Raster style presets (`_resolve_titiler_style_params` 496/679) are ADDITIVE-ONLY with disjoint keys; never mutate `continuous_flood_depth`/`continuous_plume_concentration`.
- Persistence: `write_case_manifest()`/`case_manifest_key()` are NEW siblings of the snapshot writer (persistence.py:837/84); dual-write at the 5 snapshot call-sites (server.py:3528/3626/7163/7343/8070) via `asyncio.to_thread`. Snapshot retirement is LAST + must repoint `case_export` to `case-data/*.geojson` in the same change.

## COASTAL WAVE-VISUAL ADDITIONS (NATE 2026-06-21, approved)

NATE wants the WAVES to visibly ANIMATE on the Mexico Beach demo (SnapWave wave-HEIGHT field as animated frames), not just flood depth. Verified state: the quadtree+SnapWave engine is LIVE (grace2-sfincs-quadtree, SnapWave smoke-proven), fetch_topobathy exists, forcing adapter wired -- BUT postprocess_flood emits DEPTH frames ONLY; the wave-height field is never extracted/published/animated (zero wave-height output in agent or web, confirmed by grep). Two adds to the coastal track:
- **WAVE-HEIGHT ANIMATION job** (code; queued AFTER Wave B to avoid agent-tree collision): extract the SnapWave wave-height field from the quadtree worker output -> frame sequence (mirror postprocess_flood) -> new disjoint publish_layer preset (e.g. continuous_wave_height) -> publish as its own scrubber-animated overlay; wire into model_flood_scenario's coastal quadtree+SnapWave branch. Reuses the SequenceScrubber + the Wave-A durable-vector publish gate. Scoping agent dispatched 2026-06-21 (read-only) -> execution-ready plan; build fires when Wave B lands. SCOPE RESOLVED (agent a73cd5cfc, 2026-06-21, source-verified against SFINCS Fortran ncoutput.F90): the wave field IS TIME-RESOLVED -> TRUE MOVING WAVES. `hm0` (incident sig wave height) + `hm0ig` (infragravity) are defined with a time dim and written EVERY output step, gated only on `snapwave=1` (which the live deck sets; no storehm0 keyword needed, no worker/image rebuild). It is a stationary wave-energy solver re-solved each step (field evolves with the surge forcing) - exactly NATE's "waves move" ask + the Deltares demo. PLAN PHASES: P0 dump a real quadtree sfincs_map.nc schema; P1 SHARED UGRID face->raster rasterizer in postprocess_flood (LOAD-BEARING RISK: quadtree output is face-indexed UGRID but _write_verified_cog assumes a regular (n,m) grid -> would fail on real quadtree output; this also means the DEPTH animation likely never ran on a true quadtree solve -> P1 fixes both); P2 postprocess_waves.py sibling (hm0 peak + frames, NODATA_WAVE_M=0.05); P3 continuous_wave_height style preset (additive); P4 NO contract change (rides LayerURI role=context + style); P5 wire into model_flood_scenario quadtree branch (degrade-not-fail, after depth emit ~line 2508); P6 live Mexico Beach Batch run. NO file collision with Wave B (waves = postprocess_flood/postprocess_waves/publish_layer/model_flood_scenario/execution.py; Wave B = sfincs_forcing_adapter/fetch_usgs_nwis/persistence/server/case.py). Wave code is AGENT-SIDE ONLY; grace2-sfincs-quadtree image unchanged. Full plan: scope-agent a73cd5cfc output.
- **E2E MEXICO BEACH RUN** (gated live AWS Batch quadtree job, Spot $): the proof that produces the actual picture. AOI ~(-85.75,29.55,-85.25,30.20); GTSM/CO-OPS surge + wind/pressure + topobathy. Run AFTER the wave-animation job so the produced demo includes the moving waves. Gated on NATE's go (live Batch spend).

## Wave plan (file-disjoint -> parallel)

- **WAVE A (gate):** J1 #165 Phase 0 (publish vector branch, deploy first) ; J2 Compound Phase 1 (expose `surge_forcing`) IN PARALLEL (touches only model_flood_scenario.py, no publish_layer).
- **WAVE B (4 lanes):** J3 #165 manifest schema+writer+dual-write ; J4 Compound Phase 0 spike + real discharge hydrograph ; J5 MODFLOW Phase 0 spike (RIV+SRC local mf6) ; J6 HEC-RAS SPIKE A (Linux binaries solve known-good deck).
- **WAVE C:** J7 #165 cold serving ; J8 Compound 3-driver E2E + styling/tests/live ; J9 MODFLOW RIV engine->contract->agent->postprocess->composer/demo ; J10 HEC-RAS SPIKE B (headless arbitrary-AOI mesh authoring = real GO/NO-GO).
- **WAVE D:** J11 #165 retire snapshot + repoint case_export ; J12 Compound spatially-distributed precip (gated on Phase 0 spw finding) ; J13 HEC-RAS Phase 2-7 (container->Batch->wiring->E2E) ONLY if SPIKE A+B pass.
- DEFERRED: #165 PMTiles ; MODFLOW SFR/SFT ; HEC-RAS template-AOI fallback if SPIKE B fails.

Lane file ownership (zero overlap): FLOOD = sfincs_builder/sfincs_forcing_adapter/model_flood_scenario/postprocess_flood ; GW = services/workers/modflow/* + run_modflow/postprocess_modflow/run_modflow_tool ; RAS = all new files + hec_ras.tf. Shared-edit files (append-only, serialize the line): solver.py registry dicts (258/322), publish_layer presets, contracts package. `data_fetch.py` shared fetchers = READ-ONLY reuse, do not edit.

## Highest-leverage FIRST job (verified live)

**J2 — expose `surge_forcing` in `run_model_flood_scenario`.** The internal `model_flood_scenario()` already accepts + fully threads `surge_forcing` (line 1297) through `_resolve_surge_forcing_from_fetchers` -> `_build_surge_forcing_members` -> `_emit_surge_forcing_blocks` -> deck. The LLM-facing wrapper (line 2578) OMITS the param and the call (~2735) drops it -> the entire built+tested coastal-surge + fluvial-discharge engine is UNREACHABLE from the agent. ~2-line unlock; deck byte-identical when `surge_forcing is None` (zero regression). Zero collision -> runs parallel to the J1 gate.

## Shared new capability (build once, reuse)

**Discharge-hydrograph mode** on `fetch_usgs_nwis_gauges` (IV startDT/endDT -> per-site time_series_csv, mirror CO-OPS shape) and/or `fetch_noaa_nwm_streamflow` (short_range forecast-hour loop). Today both return a SINGLE instantaneous value -> `sfincs_forcing_adapter.py:145` synthesizes a FLAT constant = the #1 compound-flood physics gap. Consumed by Compound (fluvial) AND MODFLOW-seepage (river stage/inflow).

## Per-track verdict

| Track | Effort/Risk | Gating spike |
|---|---|---|
| #165 data-island | L / med | fgb->GeoJSON at publish, durable case-data/, paints box-off from plain S3 url, no inline-render regression |
| Compound flood | L / med | ONE Batch SFINCS deck with bzs+dis+precip simultaneously solves (Cape Fear/Wilmington; canonical = Hurricane Florence 2018, Grimley 2025 / Eilander 2023); also probes setup_precip_forcing_from_grid spw support |
| MODFLOW seepage | L / med | FloPy GWF+RIV+GWT+SRC deck, local mf6 6.5.0, Normal termination + non-zero RIV leakage in cbc |
| ~~HEC-RAS~~ SHELVED | XL / high | SUPERSEDED by the GeoClaw swap (NATE 2026-06-21, see decision above). Revisit only for FEMA-grade regulatory channel hydraulics. |
| GeoClaw (replaces HEC-RAS) | M / low-med | Clawpack/PyClaw pip build in a Batch worker; setrun.py over an AOI + topo + a driver (dam-break / surge / tsunami) -> depth raster + animation via the existing postprocess/publish path. Scoped in its own sub-sprint after Wave A/B. |

Refs: [[project_baird_coastal_lecture_oceanmesh2d]] [[project_sfincs_north_star_demo]] [[project_modflow_river_seepage_demo]] [[project_hecras_engine_research]] [[project_scale_to_zero_island_architecture]] (#165). Full research: workflow `wen0ew0mv` output.
