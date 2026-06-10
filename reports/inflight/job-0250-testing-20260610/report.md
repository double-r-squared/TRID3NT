# job-0250-testing-20260610 — Stage 3 ROUND 6 (P5-only) — REPORT

**Verdict: FAIL** (p5_impact BLOCKED by a NEW downstream blocker; round-5 blocker OQ-0248-FLOOD-BUILD-VSIGS is genuinely FIXED).

Overall PASS rule = p5_impact PASS + >=2 of {analysis_count, chart_emission, chart_replay}.
p5_impact did NOT reach an ImpactPanel, so the rule is not met -> FAIL. B2/B3/B4 were
correctly SKIPPED (no impact panel to query/chart/replay).

## What was verified (LIVE, no inject seams, 2 Gemini turns, no 429)
Agent on :8765 (89 tools, started on the staging fix at 06:54), NOT restarted. Web :5173.
Harness: web/tools/stage3_p5_round6_job0250.mjs (adapted from round-5; kept settle heuristic;
B1_MAX raised to 25 min for the cloud solve; progress screenshots every ~2 min).

FRESH Case. Kickoff prompt verbatim:
"Run a flood damage assessment for Fort Myers with Pelicun using the NSI building inventory
and the existing Fort Myers flood depth layer." Harness sent the "use the NSI inventory"
clarification once (the heuristic fired on the agent's honest failure-narration; 1 extra turn,
allowed).

## The round-5 staging fix WORKS (positive result)
df7b4ba (_stage_gcs_local) is confirmed live. The round-5 failure
("No such file found: /vsigs/.../dem.tif" on a cache-hit build) is GONE. Agent log shows:
- Reading raster data from /tmp/grace2-hydromt-stage/c13f1fca....tif (gs:// input STAGED to a
  local file, then opened by rasterio - no /vsigs trip).
- All fetch_* inputs were CACHE HITS (DEM/landcover/river/precip) - the exact cache-hit path
  that broke in round 5 - and the SFINCS deck BUILT + uploaded to GCS successfully, the Cloud
  Run/Workflows solver was SUBMITTED and ran to completion (twice).

## THE new blocker - OQ-0250-POSTPROCESS-FSSPEC-NOOPCALLBACK (engine/infra, deterministic)
After each successful cloud solve, postprocess_flood failed reading the NetCDF run output:

  WARNING grace2_agent.workflows.model_flood_scenario postprocess_flood failed:
  RUN_OUTPUT_READ_FAILED (could not fetch run output
  gs://grace-2-hazard-prod-runs/<run_id>/sfincs_map.nc:
  Invalid variable type: value should be str, int or float,
  got <fsspec.callbacks.NoOpCallback object ...> of type <class 'fsspec.callbacks.NoOpCallback'>)

ROOT CAUSE (reproduced in isolation, read-only, in the agent venv):
postprocess_flood.py::_fetch_run_output (lines ~119-142) uses
fsspec.filesystem("gcs").get(uri, local) to download sfincs_map.nc. This env pairs
fsspec 2026.1.0 with gcsfs 0.8.0 (ancient). Modern fsspec passes a NoOpCallback object into
gcsfs's transfer code, which gcsfs 0.8.0 cannot handle -> TypeError: Invalid variable type.
Verified against an object that EXISTS (the run's manifest.json), so this is the fs.get() call
itself, NOT a missing object / not a 404 / not a /vsigs issue.

This is the SAME class of fsspec/gcsfs incompatibility the job-0249 staging fix worked around
for HydroMT in sfincs_builder.py (by switching to google-cloud-storage). postprocess_flood.py
was NOT patched and still uses the raw fsspec.gcs.get path -> it remained broken. Two back-to-back
cloud solves (run_ids 01KTRZ3F7E... and 01KTRZSPY7...) hit the identical error.
Owner: engine/infra (apply the google-cloud-storage download pattern to
postprocess_flood._fetch_run_output, and/or bump gcsfs).

Because postprocess never produced/published hmax.cog/flood_depth_peak.tif, the agent then
called run_pelicun_damage_assessment against LLM-predicted hazard URIs that don't exist
(gs://.../hmax.cog, gs://...-prod-temp/..., gs://.../runs/<id>/flood_depth_peak.tif) -> 404s ->
the run_pelicun_damage_assessment circuit breaker TRIPPED after 3 consecutive failures
(cooldown 60s; circuit-breaker working as designed). No flood depth layer => no Pelicun =>
no ImpactEnvelope => no ImpactPanel.

## Agent behavior - CORRECT (no logic regression)
- Routing: geocode_location -> list_categories -> discover_dataset -> list_tools_in_category ->
  fetch_usace_nsi (NSI inventory rendered as a vector layer on the Fort Myers map) ->
  run_pelicun (existing-layer path, 404) -> run_model_flood_scenario (fresh build, OK) -> solve
  (OK) -> postprocess (BUG) -> narrated honestly.
- groundwater_hit_count = 0 - NO Twin-Falls / MODFLOW / contamination regression
  (Wave-4.10/4.11 context-isolation holds).
- flood_route_hit_count = 23; map centered on Fort Myers (-81.83, 26.62, zoom 11.8).
- Honest narration ("I was unable to find the existing flood depth layer ... I will now try to
  generate it ... This may take a few minutes."). The always-narrate clause + tool-retry loop
  worked: tool errors were fed back as structured function_responses and the agent re-planned.
- UI: tool cards interleave inline in arrival order; failed run_pelicun_damage_assessment card
  tints RED, successful cards green; NSI vector layer visible in LayerPanel + map. No ImpactPanel
  (correct - none should appear without an ImpactEnvelope).
- 2 Gemini turns total; no 429; no fatal harness error. 3 console errors are benign
  (the expected hazard-download 404; a cosmetic MapLibre "glyphs" warning on the NSI
  cluster-count text layer; a pre-existing React controlled-input null warning).

## per_scenario
- p5_impact:       FAIL    (BLOCKED - postprocess_flood fsspec NoOpCallback; no ImpactPanel)
- analysis_count:  BLOCKED (skipped - no impact panel to query)
- chart_emission:  BLOCKED (skipped - no damage data to chart)
- chart_replay:    BLOCKED (skipped - nothing to replay)

## Evidence (all under reports/inflight/job-0250-testing-20260610/evidence/)
- findings.json, ws_frames.json, harness_run.log
- P02_no_impact_panel.png - final UI: NSI layer rendered, red failed Pelicun card, honest
  narration, NO ImpactPanel.
- P01_progress_t*.png - 12 progress shots across the two ~10-min cloud solves.
- Agent log (live): /tmp/agent_restart_0249.log - staged-local read, both solves, both
  postprocess RUN_OUTPUT_READ_FAILED, circuit-breaker trip.

## Recommendation
Open an engine/infra fix job for OQ-0250-POSTPROCESS-FSSPEC-NOOPCALLBACK:
postprocess_flood._fetch_run_output must download sfincs_map.nc via google-cloud-storage
(mirroring the job-0249 _stage_gcs_local pattern) instead of fsspec.filesystem("gcs").get,
or upgrade gcsfs to a version compatible with fsspec 2026.1.0. Then re-run this P5-only
round-7. The staging fix itself needs no further work - it is proven.
