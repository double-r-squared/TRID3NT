# job-0242 adversarial verify — PLAUSIBILITY lens

REFUTE-by-default reviewer. No Gemini. Artifacts only.

## Lens scope
PLAUSIBILITY: (1) plume bbox actually in Idaho; (2) concentration/area physically sane vs
release mass; (3) Fort Myers structure counts order-of-magnitude sane; (4) chart payload
structurally valid Vega-Lite.

## Verdict: CONFIRM (severity: none for this lens)

The runner's PARTIAL verdict makes NO implausible quantitative claim. Every plausibility-relevant
quantity that exists in the artifacts is physically sound; every quantity that would be assessed by
my four targets but does not exist is HONESTLY marked unavailable (FAIL/BLOCKED), not fabricated.

## Target-by-target

### 1. Plume bbox in Idaho — NO plume produced; runner does not claim one
- `findings.json` scenarios.A.plume.materialized = false; `run_console.log:19` `[A] plume:
  {"materialized":false}`. No plume layer, no plume bbox exists.
- Runner's claim is the INVERSE: map stayed over the upper-midwest, `final_map.in_idaho_bbox=false`,
  layer_ids = ["qgis-basemap","osm-fallback-basemap"] only. Confirmed by `A05_final_plume_map.png`
  (Saint Paul MN / Iowa visible, "No layers loaded yet").
- The only Idaho geo-claim that DOES exist is the gate-derived spill coordinate
  `[42.5558542, -114.4700684]`. Verified: 1.09 km from Twin Falls, Idaho center; inside the Idaho
  bbox. PLAUSIBLE and correct.

### 2. Concentration/area vs release mass — NO plume output; release-mass INPUTS are exact
- No concentration or area was produced (solve never ran). Nothing to refute.
- Gate-derived release mass IS present and physically exact:
  - total_mass_kg = 66320.41 = 12000 gal x 3.785411784 L/gal x 1.46 kg/L (correct TCE density 1.46).
  - release_rate_kg_s = 3.0704 = total_mass / (0.25 day = 6 h), matching the article's "drained over
    roughly six hours."
  - aquifer_k=0.0001 m/s, porosity=0.3 are stated demo values, flagged "NOT site-specific" in the
    caveat. Internally consistent, order-of-magnitude sane for a demo sand/gravel aquifer.

### 3. Fort Myers structure counts — BLOCKED, never ran
- Scenario B BLOCKED (harness crashed at nav, downstream of stuck gate). No Pelicun panel, no
  structure count emitted. Runner asserts no number. Nothing to assess; honestly unavailable.

### 4. Chart payload Vega-Lite — BLOCKED, never ran
- chart_emission / chart_replay BLOCKED. No chart payload emitted. Runner asserts no validity.
  Nothing to assess; honestly unavailable.

## Cross-checks on the runner's honesty (no overclaim)
- The runner did NOT invent a plume, a concentration, an area, a structure count, or a chart. All
  four would-be plausibility checks are gated behind a real upstream failure (Proceed dropped) that
  the runner documents with matching WS frame + agent-log line (`agent_log_run2.log:168-169`:
  gate emitted warning_id=01KTRGCNWKG8BY7MZK34Q5S5QW -> "unknown/closed warning_id" 1.8s later).
- 4 `connection open` events at 03:11:19-20 (`agent_log_run2.log:98-104`) corroborate the
  multi-WS-connection root cause, supporting (not contradicting) the FAIL.

## Conclusion
From the plausibility lens there is nothing to refute: no fabricated or physically-implausible
quantity appears anywhere in the bundle. The sole concrete quantities (Idaho geocode + TCE
release mass/rate) are exact and sane. All other plausibility targets are correctly reported as
not-materialized. CONFIRM.
