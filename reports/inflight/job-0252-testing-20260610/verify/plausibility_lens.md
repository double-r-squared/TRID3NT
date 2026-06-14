# job-0252 adversarial verify — PLAUSIBILITY lens

Verdict: CONFIRM (severity none). REFUTE-by-default cleared.

## What the plausibility lens can check here
The runner's verdict is PARTIAL: flood_layer_published PASS; p5_impact/analysis_count/
chart_emission/chart_replay all BLOCKED. The BLOCKED legs mean NO ImpactPanel, NO damage
numbers, NO repair costs, NO damage distribution, NO Vega-Lite chart were ever produced.
Confirmed empirically:
- ws_frames.json: grep for chart-emission|chart_spec|vega|damage_state|ImpactEnvelope|
  repair_cost|expected_annual = 0 hits.
- Frame-type census: pipeline-state x24, session-state x4, agent-message-chunk x4,
  case-list x3, case-command x2, case-open x2, map-command x2, user-message x1.
  Tool names seen: geocode_location, list_categories, list_tools_in_category,
  fetch_usace_nsi, run_pelicun_damage_assessment, run_model_flood_scenario, gemini_generate.
  NO chart / impact-envelope frame type present.
- harness_run.log line 51-52: "[P5] settled (impactPanel=0)" then
  "[P5] No impact panel; SKIPPING B2/B3/B4." -> the BLOCKED legs are genuinely skipped,
  not fabricated.
- findings.json: impact_panel_present=false, impact_envelope_frame=false, b23_skipped=true,
  b23_skip_reason=no_impact_panel.

So there are NO synthesized impact/cost/chart numbers to be implausible. The lens's main
risk (fabricated-but-implausible damage stats) is moot — nothing was fabricated.

## The ONE produced artifact: flood layer over Fort Myers — PLAUSIBLE
- bbox (-81.9126, 26.5476, -81.7511, 26.6892); center (-81.832, 26.618), zoom 11.83.
  Fort Myers canonical ~26.640 N / -81.872 W. Center is squarely in the Fort Myers /
  south Cape Coral metro. CONFIRM: bbox IS Fort Myers.
- bbox spans ~16.0 x 15.7 km = ~252 km2 (Fort Myers metro slice incl. Caloosahatchee R).
- flood layer id flood-depth-peak-01KTS2HNC393Q9PZ563ASB96AA matches the report's run_id.
- Colorbar 0-3.5 m max flood depth: plausible pluvial/coastal flood depth range for a
  100yr/24hr FL event (report cites 11.9 in @100yr/24hr forcing). SANE.
- Screenshots P01b/P02 confirm: LayerPanel "USACE NSI Structures" + "Flood Depth (peak)"
  + "Max flood depth (m) 0 m - 3.5 m" colorbar; map on Fort Myers w/ Caloosahatchee River;
  NSI structures as green clusters; NO ImpactPanel; honest agent narration (no fake damage
  numbers in chat).

## NSI inventory order-of-magnitude — SANE
- Report claims 70,740 NSI features for the bbox -> ~281 structures/km2 over ~252 km2.
  Plausible for a developed FL metro (mixes dense urban core + suburban + some water/ag).
- Sampled inline_geojson structures (commercial):
    COM4  96,378 sqft  $13.9M repl  = $144/sqft
    COM4  16,467 sqft  $ 2.07M repl = $126/sqft
    COM7  28,761 sqft  $ 2.74M repl = $ 95/sqft
  $95-144/sqft for commercial replacement value is in the credible NSI range (low-to-mid
  end of US commercial construction). occtypes (COM4 retail, COM7 medical), found_type S,
  ground_elv ~26-29 ft, FL census block FIPS 1207xxxx (Lee County, FL = Fort Myers). All
  internally consistent and geographically correct.

## NSI feature geometry sanity
- Sampled coords (-81.753, 26.550), (-81.755, 26.555) fall inside the stated bbox. SANE.

## Page errors — not a plausibility concern, consistent with story
- The 400/AJAXError WMS GetMap floods are all for flood-depth-peak-01KTS2HNC... = the QGIS
  periodic-cache cold-start lag the kickoff explicitly waived (job-0245 USER_UNBLOCK.md).
  Consistent with "publish CONDITION_SUCCEEDED but GetCapabilities not yet listing it."
  The maplibre cluster-count glyphs warning is a pre-existing NSI vector-style nit, unrelated.

## Out-of-lens (deferred to other panelists)
- Whether the iter-9 Pelicun URI was genuinely hallucinated (gs://...-cache/...postprocess_
  flood/a819775f....tif) vs the real gs://...-runs/01KTS2HNC.../flood_depth_peak.tif is an
  agent-logic / log-forensics question (report cites /tmp/agent_restart_0251.log, not in
  the evidence dir). NOT a plausibility judgement. The agent-message-chunk frame "I am
  unable to access the flood depth layer ... I will run a new flood simulation" corroborates
  the narrated recovery, but the 404 itself is outside this lens.

## Conclusion
Every number/artifact that WAS produced is order-of-magnitude sane and geographically
correct for Fort Myers. The BLOCKED legs produced nothing, so there is no implausible
fabrication. PARTIAL verdict is faithful to the artifacts from the plausibility angle.
