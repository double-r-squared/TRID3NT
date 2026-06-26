# OpenQuake classical PSHA - local proof + deploy (NATE 2026-06-26)

Engine-coverage track step 1 ([[project_engine_scenario_coverage_track]]). "Knock out
OpenQuake functionality" - test LOCALLY first, deploy when it works, NATE prod-tests via Haiku.

## What was done
Installed the real `openquake.engine 3.20.1` (the worker's pinned version, GEM py311
wheelhouse constraints) in a throwaway venv and ran the repo's OWN generated deck through
`oq engine --run` for the FIRST time. The live run exposed 4 bugs the string-matching unit
tests could never catch; all fixed + re-proven end to end.

## The 4 bugs (only a real engine run finds these)
1. `source_model.xml` declared NRML xmlns **0.5** but the area-source body is the **0.4**
   schema -> `InvalidFile: ... should be xmlns .../nrml/0.4`. Fixed: declare 0.4.
2. `gml:posList` written **lat lon**; OQ's area-source parser reads **lon lat** -> it read a
   longitude as a latitude (`latitude -122.45 < -90`). Fixed: emit lon lat.
3. `region_grid_spacing` was km->**deg** converted (~0.18 for 20 km), but OQ's unit is **km**
   -> a ~100x-too-fine grid (12477 sites for a 0.2 deg AOI vs ~4) = absurdly slow/costly on a
   real AOI. Fixed: pass the km value directly.
4. postprocess `rasterize_hazard_sites` assumed a clean lat/lon lattice; OQ's km-grid offsets
   lon by latitude (~3e-5 deg jitter/row) -> 34 "columns" for a real ~5 (striped raster +
   area_km2=0). Fixed: cluster near-duplicate axis values.

## Local proof (the deploy gate)
- `oq engine --run job.ini` on the repo deck -> RUN_EXIT=0, hazard_map CSV + hazard_curve CSV.
- Chain: CSV -> parse -> rasterize (clean 7x5 grid, 0.045 deg) -> metrics (max **0.7953 g**,
  area **673.6 km2**, 34 sites) -> valid EPSG:4326 COG. Figure surfaced to NATE.
- Tests green: worker deck 11, agent oq 15, contracts 6, gate suites + physics_registry (71).
  Tests updated off the old buggy assertions to engine-verified values.

## Gaps closed (agent-side)
- `run_seismic_hazard_psha` added to SOLVER_CONFIRM_TOOLS -> proceed/cancel confirm card +
  autostop solver-busy marker (Invariant 9). No resolution picker (classical PSHA = area
  source over the AOI).
- physics_registry `width_of_mfd_bin` default 0.1 -> 0.2 (matches the deck / engine-proven).

## Deploy (all live)
- Worker image: CodeBuild `grace2-worker-builder` (WORKER_DIR=openquake) rebuilt
  `grace2-openquake:latest` (377 MB, deck fixes) + pushed to ECR.
- Batch job-def `grace2-openquake` ACTIVE, image `grace2-openquake:latest` (auto-picks the
  new image next run). Queue grace2-solvers. Env `GRACE2_AWS_BATCH_JOB_DEF_OPENQUAKE=grace2-openquake`
  already set on the box (75-new-engines.conf).
- Agent code swapped to HEAD via SSM (postprocess rasterize + confirm gate + MFD). Verified on
  box: `run_seismic_hazard_psha in SOLVER_CONFIRM_TOOLS = True`, rasterize cluster present,
  service active, 140 tools. Box stopped for clean baseline.

Commits: 4fa3691 (4 deck/postprocess fixes) + a53e187 (confirm gate + MFD).

## NATE prod-test (via Haiku)
Open a case, draw/geocode an AOI, ask for a seismic hazard map / PSHA (e.g. "probabilistic
seismic hazard, PGA 10% in 50 yr, for this area"). Expect: a proceed/cancel confirm card ->
Batch grace2-openquake run -> a PGA hazard COG layer + narration (max PGA, hazard area,
return period). Hint: keep the AOI modest; coarse spacing renders blocky (a real demo AOI
finer). First wake clears a stale busy marker.

## Deferred (follow-on, NOT this pass)
- Seismic RISK archetypes: scenario_damage / scenario_risk / event_based_risk (GEM exposure +
  vulnerability + Vs30 + source-model fetchers). Classical PSHA stops at hazard; real source
  model / Vs30 grid is hardcoded demo (single bbox area source, G-R a=4/b=1, BooreAtkinson2008,
  flat Vs30=760) - acceptable for a first functional hazard map.
- Hazard-curve / UHS publishing (publish_openquake_quantities is dead in the live path).
- The "incident area / rupture" user-input gate ([[feedback_never_fabricate_model_inputs_user_gate]])
  is for SCENARIO mode (not built); classical PSHA needs no rupture pick.
