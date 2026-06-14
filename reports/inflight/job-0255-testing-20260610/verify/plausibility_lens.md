# job-0255 adversarial verify — PLAUSIBILITY lens

Verdict: **CONFIRM** the runner's overall FAIL.

## Lens scope
PLAUSIBILITY = are Pelicun's outputs order-of-magnitude sane: Fort Myers structure
count, repair costs, damage-state distribution, Vega-Lite validity.

## Decisive finding
**Pelicun produced ZERO output. There is nothing to plausibility-check.** A plausibility
PASS is impossible to satisfy because no damage numbers, no distribution, no chart spec
ever existed. The runner did NOT fabricate any plausible-looking damage figures — the
artifacts contain none, and the report does not assert any. That is the correct, honest state.

### Artifact evidence
- `ws_frames.json` (48 frames): `impact`=0, `ImpactEnvelope`=0, `damage_state`=0,
  `expected_loss`=0, `agent-message`=0 occurrences. Frame-type tally has NO
  `impact-envelope` and NO terminal `agent-message-chunk`. Last pipeline-state
  (t=1476566ms) shows a *second* `run_model_flood_scenario` stuck `running` — the loop
  hit MAX_TURN_ITERATIONS=12 and the socket closed (1001 going away). No impact phase reached.
- `agent_log_p5_turn.txt:170-202`: the single `run_pelicun_damage_assessment` call
  (iter 11) was fed `hazard_raster_uri=https://grace-2-qgis-server-.../ogc/wms?MAP=...&LAYERS=flood-depth-peak-01KTS8H8RJT6311A2V4BKX6H8A`
  — a QGIS Server WMS GetMap endpoint, NOT a gs:// COG and NOT a mangled gs:// path.
  `_download_uri_to_local` raised `PelicunRuntimeError: local path does not exist`.
  The path-mangle repair guard (commit 6804588) never fired because the input was not a
  gs:// URI at all — out of the guard's scope. Confirms `pelicun_download: FAIL`,
  `p5_impact: FAIL`, and `analysis_count`/`chart_emission`/`chart_replay: BLOCKED`.
- `P02_no_impact_panel_flood_nsi_rendered.png`: Fort Myers basemap with green NSI points
  + flood-depth layer rendered; chat shows the prompt and tool cards; **no ImpactPanel,
  no headline damage numbers**. Matches `p5_impact: FAIL`.

## NSI inventory plausibility (the only quantitative artifact present — it IS sane)
From `uri_events.json` inline GeoJSON sample + agent log `fetch_usace_nsi`:
- 70,740 structures for the Fort Myers bbox (-81.913,26.548,-81.751,26.689) — metro-scale,
  order-of-magnitude sane for densely-developed Lee County FL coastal.
- replacement_value range $127k (small GOV1) .. $24.3M (173k-sqft 3-story EDU1 school w/
  1037 students) — realistic commercial/institutional spread.
- sqft 797 .. 173,033; year built 2003/2017; slab foundation (found_ht 0.5 ft) — typical
  FL coastal new construction. No implausible inputs detected.
This is the *input* to Pelicun, which the chain never consumed (download failed before
fragility curves were applied). So even the inventory plausibility cannot rescue the run.

## No contradiction of the runner verdict under this lens
Nothing in the runner claim asserts a plausible-but-wrong Pelicun number; the claim is
that Pelicun never ran. The artifacts fully corroborate. Refute-by-default finds no
fabricated or implausible quantitative claim to overturn the FAIL.
