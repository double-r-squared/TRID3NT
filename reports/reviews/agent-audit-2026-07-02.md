# services/agent maintenance audit — 2026-07-02

Four parallel Sonnet reviewers (concurrency, error-handling/honesty, session/state/durability, solver/tooling/resources) over `services/agent/src/grace2_agent`. ~35 raw findings, deduped + themed below. Severity is the reviewer's; **[verified]** = orchestrator spot-checked against code; **[reported]** = reviewer-cited, not yet re-verified.

## THEME A — Honesty-floor violations (status=ok with empty/zero layers) — HARD NORM
- **A1 HIGH** `workflows/model_flood_scenario.py:~4838` + register-only path ~4395: all-`publish_layer`-fail -> `envelope_type="modeled"`, `layers=[]`, real metrics, no `:FAILED:` -> reads ok. [reported]
- **A2 HIGH** `workflows/model_nws_flood_event_scenario.py:644`: `_detect_flood_failure` can say failed but return block hardcodes `status="ok"`. [reported]
- **A3 CRITICAL** `workflows/model_goes_fire_animation.py:831`: wrapper passes `pipeline_emitter=None`; frames never pushed to loaded layers -> success response, empty map. GLM + satellite-fire got the fix; GOES-fire didn't. [reported]
- **A4 HIGH** `workflows/postprocess_openquake.py` + `model_seismic_hazard_scenario.py`: no empty/sub-floor guard -> all-NaN COG returned ok. (MODFLOW has MODFLOW_ARCHETYPE_EMPTY_RESULT; OQ doesn't.) [reported]
- **A5 HIGH** `workflows/postprocess_waves.py` (SnapWave): all-calm wave field -> all-NaN COG published ok. (postprocess_swan raises SWAN_OUTPUT_EMPTY; SnapWave doesn't.) [reported]
- **A6 MEDIUM** `tools/run_geoclaw_tool.py` / `run_landlab_tool.py` / `run_swmm_tool.py`: no zero-metric honesty floor (max_depth=0 / FoS=0 etc. returned ok). [reported]
- **A7 MEDIUM** `workflows/sfincs_builder.py:734`: all-nodata NLCD -> warn+return, HydroMT uses fabricated uniform Manning -> modeled result on silently-wrong params. [reported]

## THEME B — Swallowed CRS reproject -> layer plotted at (0,0)/wrong location
- **B1 HIGH** `workflows/postprocess_flood.py:676`: `ds["x"]/["y"]` read failure -> `Affine.identity()` -> COG georeferenced at origin, tiles served wrong location, no error. [reported]
- **B2 HIGH** `workflows/run_modflow.py:544`: `to_crs("EPSG:4326")` in bare `except: pass` -> river coords left in metres -> phantom river km away, deck builds + runs. [reported]
- **B3 HIGH** `pipeline_emitter.py:653` `_fgb_bytes_to_geojson`: `to_crs` failure logged but continues with projected coords -> vector plotted far off. [reported]
- **B4 MEDIUM** `workflows/postprocess_modflow.py:968` + 1043: FloPy load fail -> geo=None -> identity transform + hardcoded 2500 m2 cell -> plume at UTM origin, wrong area. [reported]

## THEME C — Temp-dir / rundir leaks -> agent-box disk fill over time
- **C1 HIGH** `tools/solver.py:~1479` local rundir never rmtree'd (largest per-run footprint; live MODFLOW local-exec + local-docker SFINCS). [reported]
- **C2 HIGH** `workflows/postprocess_modflow.py:809,835,1493,1519,1705,1731`: six ucn/cbc/hds download dirs never cleaned. [reported]
- **C3 HIGH** `workflows/postprocess_flood.py:144`: sfincs_map.nc download dir never cleaned. [reported]
- **C4 HIGH** `workflows/run_modflow.py:534`: river-geometry mkdtemp never cleaned. [reported]
- **C5 MEDIUM** `workflows/postprocess_flood.py:706` `_finalize_cog`: NamedTemporaryFile(delete=False) leaks on the error path. [reported]

## THEME D — Session / state / durability
- **D1 CRITICAL** `server.py:~4515-4531`: case-open rehydrate re-assigns `state.chat_history` but never `emitter.seed_chat_history(...)`; the reinline follow-up `emit_session_state` ships stale empty history -> chat transcript BLANKS after opening a Case with a vector layer. Bare-reconnect path does seed; case-open doesn't. [reported — HIGH CONFIDENCE, judge-visible]
- **D2 HIGH** `server.py:3771` session-resume `_rebind_live_turns(..., only_turn_key=None)` rebinds ALL cases' live turns + merges their layers -> Case B layers show while viewing Case A. (case-open path passes only_turn_key correctly.) [reported]
- **D3 MEDIUM** `server.py:4447,4674,4737,4359`: turn_count reset to 0 on every case-open incl. re-opening the same Case -> MAX_TURNS cap bypass. [reported]

## THEME E — Concurrency / event-loop
- **E1 MEDIUM** `server.py:1859` **[verified]**: `await asyncio.sleep` outside the try/except in `_run_idle_exit_monitor`; CancelledError permanently kills the self-idle-exit (the orphan-leak guard) silently. Cheap fix. Low-probability trigger.
- **E2 HIGH** `pipeline_emitter.py:1167,1174`: `rebind_sink` discards `loop.create_task(...)` handles (no strong ref) -> pipeline-replay-on-reconnect can be GC'd. Codebase's `_BG_SNAPSHOT_TASKS` pattern not applied here. [reported]
- **E3 LOW/MEDIUM** `pipeline_emitter.py:793` density-meta dict mutated from worker thread, read on loop -> race. **Box-only**; harmless on current single-session Fargate (both concurrency + session reviewers agree). Low priority.

## THEME F — fetch_topobathy cache-hit drops bathymetry-absent warning
- **F1 HIGH** `tools/fetch_topobathy.py:1594` **[verified]**: `_flags` defaults `bathymetry_present=True`; `_fetch()` only runs on cache MISS, so a cached ETOPO-fallback DEM returns claiming bathy present + no warning -> coastal scenario floods nothing, unexplained. Needs the flags persisted with the cached artifact (sidecar) or re-derived on hit.

## THEME G — Stale GCP references (ties to in-flight task #142 STORAGE lane)
- **G1 HIGH** `tools/run_modflow_tool.py:295` + `run_modflow_archetype_tool.py:262`: fallback `f"gs://{_runs_prefix()}/..."` (gs:// on AWS) when output_uri None. [reported]
- **G2 MEDIUM** `tools/run_modflow_tool.py:397`: `_runs_prefix()` default = decommissioned `grace-2-hazard-prod-runs` GCS bucket. [reported]

## THEME H — Error classifier / typed-error gaps
- **H1 HIGH** `tools/solver.py:2835`: `NoSuchBucket` grouped with `NoSuchKey` -> silent None -> full 30-min SOLVER_TIMEOUT instead of surfacing a wrong bucket. [reported]
- **H2 HIGH** `tools/fetch_topobathy.py`/`data_fetch.py:1026`: `_fetch_msft_buildings_bytes` returns empty FeatureCollection bytes instead of raising UpstreamAPIError -> fabricated .fgb cached, FGB readers crash; OSM->MSFT fallback never triggers. [reported]
- **H3 MEDIUM** `tools/fetch_gtsm_tide_surge.py:591`: missing-CDS-key classifier misses "incomplete configuration file" -> credential card never fires, infinite retry. (ERA5 fixed this; GTSM didn't.) [reported]
- **H4 MEDIUM** `tools/fetch_gtsm_tide_surge.py:634`: `zf.namelist()` after the `with` block closed -> ValueError instead of typed GTSMUpstreamError. [reported]
- **H5 MEDIUM** `tools/fetch_noaa_nwm_streamflow.py:465,487`: total NLDI outage swallowed -> "no NHDPlus rivers" retryable=False (rivers reported permanently absent on a transient outage). [reported]
- **H6 MEDIUM** `workflows/model_groundwater_contamination_scenario.py:897`: error_code baked into message string, `exc.error_code` stays generic class default -> telemetry/retry see wrong code. [reported]
- **H7 MEDIUM** `bedrock_adapter.py:889`: streamed tool-call JSONDecodeError swallowed -> args={} -> tool crashes (root cause invisible) or runs with wrong defaults. [reported]
- **H8 MEDIUM** `workflows/publish_quantities.py:194` / `run_geoclaw.py:~674`: unguarded reader / source-resolve returns None on transient error indistinguishable from legit empty. [reported]

## THEME I — publish success but tiles 404
- **I1 HIGH** `tools/publish_layer.py:2373` (S3/TiTiler branch): no object-existence validation (GCS branch has `_validate_and_correct_layer_uri`); a wrong s3:// key returns a tile URL that 404s every tile, reported as published. [reported]

---
### Confirmed non-bugs (reviewers checked): GeoClaw scenario cleans out_dir in finally; SWMM cleans tmp; per-turn Batch inflight tracking wired correctly from the cancel path; `_ensure_emitter` empty stub at server.py:7351 is dead (real impl at 7358) — cosmetic only.
