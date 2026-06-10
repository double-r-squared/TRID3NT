# job-0252-testing-20260610 — Stage 3 ROUND 7 (P5 FINAL) — REPORT

**Verdict: PARTIAL** (flood_layer_published PASS; p5_impact BLOCKED by a NEW agent-logic blocker
OQ-0252-PELICUN-URI-NOT-WIRED; the round-6 blocker OQ-0250 postprocess fix is PROVEN end-to-end).

Overall PASS rule = flood_layer_published PASS + p5_impact PASS + >=2 of {analysis_count,
chart_emission, chart_replay}. p5_impact did NOT reach an ImpactPanel -> rule not met -> NOT a
full PASS. flood_layer_published is a clean PASS and the postprocess fix is proven, so this is a
PARTIAL, not a FAIL.

## Headline — the OQ-0250 postprocess fix (commit 0b35791) is PROVEN live
The round-6 blocker (gcsfs 0.8.0 NoOpCallback crash in postprocess_flood) is genuinely FIXED.
A FRESH SFINCS solve ran to completion and postprocess succeeded for the FIRST TIME in the live
loop. Agent log (run_id 01KTS2HNC393Q9PZ563ASB96AA, session 01KTS2BGXW30YBZ304JCG6W7YQ):
- 08:40:22 postprocess_flood: flipping rows ... Y-axis flip applied (job-0086) — postprocess RAN.
  NO RUN_OUTPUT_READ_FAILED, NO NoOpCallback TypeError. Read the NetCDF run output via
  google-cloud-storage (the fix).
- 08:40:24 uploaded flood-depth COG to gs://grace-2-hazard-prod-runs/01KTS2HNC.../flood_depth_peak.tif
- 08:40:24 publish_layer layer_id=flood-depth-peak-01KTS2HNC... style=continuous_flood_depth
- 08:43:21 publish_layer execution completed state=CONDITION_SUCCEEDED
- 08:43:21 model_flood_scenario complete envelope_id=01KTS37N4VYXVKPXFY83BZND6F run_ids=['01KTS2HNC...'] layers=1
Venv harmonized in the running agent: fsspec 2026.1.0 / gcsfs 2026.1.0 / google-cloud-storage 3.11.

## flood_layer_published — PASS (UI-confirmed, live, no inject seams)
findings.json: flood_layer_published=true, flood_layer_renders_on_map=true,
map_has_flood_overlay=true, layer_panel_has_flood=true, map_command_count=2.
- MapLibre style carries a raster layer+source flood-depth-peak-01KTS2HNC393Q9PZ563ASB96AA
  (map-command fired by the agent).
- LayerPanel shows "Flood Depth (peak)" + "USACE NSI Structures" + the "Max flood depth (m)
  0 m - 3.5 m" colorbar (P01b_flood_layer_map.png, P02_no_impact_panel.png).
- Map centered on Fort Myers (-81.832, 26.618, zoom 11.83).
- QGIS WMS tile-serving lag (KNOWN user-gated item, job-0245 USER_UNBLOCK.md): the new layer is
  in the served .qgs (publish CONDITION_SUCCEEDED + COG uploaded) but GetCapabilities does not yet
  list flood-depth-peak-01KTS2HNC... (count=0) and the overlay shows opacity 0% / no tiles — the
  periodic project-cache had not re-parsed at screenshot time. Per kickoff this is NOT a scenario
  failure; the publish + map-command + LayerPanel legs are proven.

## THE new blocker — OQ-0252-PELICUN-URI-NOT-WIRED (agent-logic, deterministic, NOT the fix)
After run_model_flood_scenario (iter=8) returned the published layer + run_id, the agent did NOT
extract the real hazard raster URI from the workflow result. At iter=9 it re-called
run_pelicun_damage_assessment with the SAME hallucinated cache path it had guessed pre-build:
  hazard_raster_uri=gs://grace-2-hazard-prod-cache/cache/static-30d/postprocess_flood/a819775f10b78c2278d2f2e560155b75.tif
-> 404 "No such object". The real, just-produced hazard COG was
  gs://grace-2-hazard-prod-runs/01KTS2HNC393Q9PZ563ASB96AA/flood_depth_peak.tif.
The agent never used it -> Pelicun 404 -> no ImpactEnvelope -> no ImpactPanel.
This is a DIFFERENT, downstream defect from OQ-0250: the model->Pelicun handoff. The flood
workflow result either does not surface the hazard_raster_uri in a form the LLM reliably copies,
or the agent prompt does not instruct it to thread the model's output URI into Pelicun.
Owner: agent. Recommended fix: have run_model_flood_scenario's tool result expose the hazard
COG/hmax URI as a top-level, prominently-named field (e.g. hazard_raster_uri), and/or have the
composer auto-chain Pelicun off the flood result so the LLM never has to guess a GCS path.
(Same root failure mode seen across rounds 6 & 7 — the agent guesses GCS URIs for Pelicun rather
than reading them from prior tool outputs.)

## Agent behaviour — otherwise CORRECT (no logic regression)
- Routing recovery is healthy: fetch_usace_nsi out-of-hot-set -> list_categories -> wrong
  category id -> structured error -> correct damage_assessment/flood_infrastructure widening
  -> NSI fetch succeeds (70,740 features, cache hit, inlined GeoJSON vector layer on map).
- After the first Pelicun 404 the agent honestly narrated and self-corrected to BUILD the flood
  model (iter=8) — the proven recovery path. Full SFINCS chain ran: all inputs cache-warm
  (DEM/landcover/river/precip 11.9in@100yr/24hr), gs:// inputs staged locally (job-0249 fix
  holding), deck built+uploaded, cloud solve submitted+completed (~9 min), postprocess (FIX),
  publish (CONDITION_SUCCEEDED).
- groundwater_hit_count=0 — NO Twin-Falls/MODFLOW/contamination regression (context isolation
  holds). flood_route_hit_count=15. Tool cards interleave inline; failed Pelicun cards tint red.
- Honest narration throughout. 0 clarification turns needed (turns_sent=1); no 429; no rate limit.

## per_scenario
- flood_layer_published: PASS    (postprocess fix proven; COG published; layer rendered on map + LayerPanel)
- p5_impact:             BLOCKED  (NEW OQ-0252-PELICUN-URI-NOT-WIRED; Pelicun fed hallucinated URI -> 404 -> no ImpactPanel)
- analysis_count:        BLOCKED  (skipped — no ImpactPanel to query)
- chart_emission:        BLOCKED  (skipped — no damage data to chart)
- chart_replay:          BLOCKED  (skipped — nothing to replay)

## Harness note (fixed this round)
Round-6's settle heuristic latched 1s after the clarification send because it gated on the FIRST
prompt's send-time + a stale quiesce window. Round-7 harness
(web/tools/stage3_p5_round7_job0252.mjs) gates settle on the MOST-RECENT send (lastSendT) AND
requires new inbound frames after a send. With the fix the harness correctly waited the full
~920s through the live SFINCS solve + postprocess + publish (run=1 the whole time) and only
settled after the flood layer published. First run aborted early due to a nohup & wrapper
orphaning issue (re-run launched as the background task itself); the first session's turn-2 also
demonstrated that closing the browser mid-stream wedges the agent's generation coroutine (no
consumer) — not a defect under test, but worth noting for harness design.

## Evidence (reports/inflight/job-0252-testing-20260610/evidence/)
- findings.json, ws_frames.json, harness_run.log
- P01b_flood_layer_map.png — flood layer published: LayerPanel "Flood Depth (peak)" + NSI +
  0-3.5 m colorbar; map on Fort Myers; agent narration + red failed-Pelicun card; NO ImpactPanel.
- P02_no_impact_panel.png — same final state.
- P01_progress_t*.png — progress shots across the ~15-min live chain (running pipeline card).
- Agent log (live): /tmp/agent_restart_0251.log — session 01KTS2BGXW... full chain incl. the
  proven postprocess + publish and the iter-9 hallucinated-URI Pelicun 404.

## Recommendation
Open an agent fix job for OQ-0252-PELICUN-URI-NOT-WIRED: thread run_model_flood_scenario's real
hazard COG URI (gs://...-runs/<run_id>/flood_depth_peak.tif) into run_pelicun_damage_assessment
— either by exposing it as a clearly-named top-level result field the LLM reliably copies, or by
auto-chaining Pelicun in the composer so no GCS path is guessed. The OQ-0250 postprocess fix
needs NO further work — it is proven end-to-end (postprocess -> COG -> publish -> map render).
Then re-run this P5-only round-8 to capture the ImpactPanel + B2/B3/B4.
Separately, the QGIS periodic-cache cold-start for new layers remains the user-gated item
(job-0245 USER_UNBLOCK.md).
