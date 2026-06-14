# job-0253 adversarial verify — PLAUSIBILITY lens

Verdict: **CONFIRM** the runner's FAIL. Severity: critical (P5 chain still broken).

## Lens scope
Fort Myers structure counts order-of-magnitude sane; repair costs plausible; damage
distribution sensible; Vega-Lite valid.

## Core finding (artifact-anchored)
The plausibility-relevant DELIVERABLES (Pelicun damage counts, repair $, damage-state
distribution, Vega-Lite chart) **do not exist**. Both `run_pelicun_damage_assessment`
calls 404'd → 0 ImpactEnvelopes → no ImpactPanel → no chart. There is literally nothing
on the output side to plausibility-check. So plausibility of damage numbers is BLOCKED by
absence, and the runner's FAIL is uncontradicted.

### URI mangle (root cause), exact artifact strings
- Published COG (agent_restart_0253.log:407):
  `gs://grace-2-hazard-prod-runs/01KTS5W9GTE7A7WPC3BNBE10EQ/flood_depth_peak.tif`
- Fed to Pelicun (agent_restart_0253.log:475 args; echoed :477):
  `gs://grace-2-hazard-prod-runs/runs/01KTS5W9GTE7A7WPC3BNBE10EQ/flood_depth_peak.tif`
- Agent inserted an extra `runs/` segment between bucket and run_id. This is in the
  LLM-supplied `args=` dict (server function-call log), i.e. agent-side, NOT tool-side.
- Result: 404 GET ...grace-2-hazard-prod-runs/o/runs%2F01KTS5W9...%2Fflood_depth_peak.tif
  (log:478, :523, :554) — twice (iter=8 and iter=9, log:475 and :557). Matches blocker
  OQ-0253-PELICUN-URI-RUNS-PREFIX-MANGLE.
- Runner's note that the verbatim-copy contract can't be honored is corroborated: the
  flood layer's surfaced `uri` in session-state is the **WMS URL**
  (`https://...qgis-server.../ogc/wms?...LAYERS=flood-depth-peak-01KTS5W9...`), not the
  gs:// COG — so the agent has no verbatim gs:// to copy and reconstructs one (badly).

### pipeline-state sequence (ws_frames.json) corroborates
run_model_flood_scenario:complete → geocode → list_categories → list_tools_in_category →
fetch_usace_nsi:complete → run_pelicun_damage_assessment:**failed** (957377ms) →
run_pelicun_damage_assessment:**failed** (996262ms). No postprocess_pelicun /
compute_impact_envelope / generate_damage_distribution INVOCATION (the 3 grep hits in the
log are the tool-registry list line only).

### No impact/chart frames
grep over ws_frames.json for impact-envelope|impact-panel|vega|chartstack|
generate_damage_distribution|damage_state|repair_cost|loss_total → **zero hits**.
P02_no_impact_panel.png shows NSI points + flood layer on map, chat tool cards, NO
ImpactPanel slide-out. findings.json: turns_sent=2, fatal_error (page closed during a
waitForTimeout), rate_limited=false.

## What IS plausible (upstream assets only — not the gated output)
NSI inline_geojson (session-state) is sane and not fabricated:
- occtypes COM4/COM7/GOV1/EDU1/IND6 — valid NSI codes.
- replacement_value $95.5K (small IND6) → $24.30M (173,033-sqft 3-story EDU1 school).
- $/sqft ratios sane: $13.92M/96,378sqft ≈ $144/sqft; $24.30M/173,033sqft ≈ $140/sqft.
- coords ~(-81.755, 26.555) = correct Fort Myers, FL; bbox -81.913..-81.751 / 26.548..26.689.
This affirms the inputs are realistic — but the Pelicun damage OUTPUT the plausibility
lens targets never materialized.

## Honesty check
Agent narration (agent-message-chunks + log) honestly reports the flood success and the
Pelicun failure ("However, my first [attempt failed]..."). NO fabricated damage numbers,
NO fake ImpactPanel, NO hallucinated repair costs. So there is no plausibility VIOLATION
to flag either — the agent did not invent sane-looking-but-false numbers; it produced none.

## Per-scenario consistency with runner
- uri_discipline FAIL — CONFIRMED (runs/ prefix mangle, 404 x2).
- p5_impact FAIL — CONFIRMED (0 ImpactEnvelopes, no ImpactPanel; P02 + WS frames).
- analysis_count / chart_emission / chart_replay BLOCKED — CONFIRMED (no damage data to
  query/chart/replay; only 2 turns sent; page closed).
- flood_layer_published_incidental PASS — CONFIRMED (COG uploaded :407, publish_layer
  dispatched :409; OQ-0250 postprocess fix holds a 3rd time).

## No contradiction found
Refute-by-default: I searched for any sane damage figure, any ImpactEnvelope, any chart,
any non-404 Pelicun response that would let me overturn FAIL. None exists. CONFIRM FAIL.
