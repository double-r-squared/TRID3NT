# job-0244 adversarial verify — PLAUSIBILITY lens

Verdict: **CONFIRM** (severity: minor). The runner's PARTIAL verdict and per-scenario claims
are corroborated by the artifacts on every plausibility axis I can test. No artifact contradicts
the runner's numbers. The one minor concern is the runner's own honest-narration flag, which it
already disclosed (not a hidden contradiction).

## Lens checklist results

### 1. Plume bbox in Idaho — CONFIRM
- `findings.json` ws_frames tool-payload-warning `tool_args.spill_location_latlon = [42.5558542, -114.4700684]`.
- `agent_log_round2.log:119,124` geocode_location 'Twin Falls, Idaho' -> resolved
  'Twin Falls, Twin Falls County, Idaho, 83301, United States' (cache_hit False then True — real geocode).
- 42.5558N / -114.4700W is inside Idaho's bbox (lat 42.0–49.0, lon -117.24 to -111.04). Verified.
- Caveat (not a contradiction): the COG itself was never published (publish_layer failed), so its
  raster bbox is not directly checkable. But the solver INPUT coords are unambiguously Twin Falls,
  Idaho, and `agent_log:131` uploads `plume_concentration_4326.tif` (EPSG:4326) — the runner does not
  over-claim a rendered Idaho overlay; it explicitly marks `case2_plume = FAIL` and `materialized=false`.

### 2. Concentration / area sane vs release mass — CONFIRM
- Mass arithmetic is EXACT: 12,000 gal × 3.78541 L/gal × 1.46 kg/L (TCE) = 66,320.4 kg, matching
  `tool_args.total_mass_kg = 66320.41445568` to 5 sig figs.
- Internal consistency: release_rate 3.0703895581 kg/s × 0.25 d (21,600 s) = 66,320.41 kg = total_mass. Exact.
- `agent_log:130,134,135` postprocess_modflow: max_concentration_mgl=2946.32, plume_area_km2=0.0125.
- Physical sanity: for the 12,500 m² plume footprint at porosity 0.3, the mean-conc-if-uniform across
  plausible aquifer thicknesses (5 m -> 3537 mg/L, 10 m -> 1769 mg/L) brackets the reported peak
  2946 mg/L correctly (peak >= mean for the thicker case, <= mean for the thinner). The 2946 mg/L is a
  source-zone/DNAPL number (above TCE's ~1100–1400 mg/L dissolved-phase solubility), which is exactly
  what you'd expect for a peak near the release point in a small near-source demo plume. Area
  0.0125 km² is a sane near-source footprint for a ~few-minute mf6 solve (exit=0, converged=True).

### 3. Fort Myers counts order-of-magnitude sane — N/A (BLOCKED, honest)
- Scenario B never produced any structure count. `findings_BC.json` = `{"scenarios":{"B":{}}}`.
- `SCENARIO_B_loop_stall.md` + `agent_log:162,165` confirm the flood scenario died at SFINCS deck-build
  (LANDCOVER_READ_FAILED, GDAL /vsigs/ InvalidCredentials) then the loop went idle (0% CPU).
- No Pelicun, no ImpactPanel -> no count to plausibility-check. BLOCKED verdict is honest; nothing fabricated.

### 4. Chart payload valid Vega-Lite — N/A (BLOCKED, honest)
- No chart payload exists anywhere in the BC artifacts (grep for vega/chart/spec/damage_state in
  ws_frames_BC.json + findings_BC.json returns only the B1 user-message). chart_emission BLOCKED is correct.

### 5. Replayed chart identical to original — N/A (BLOCKED, honest)
- No chart was emitted, so no replay. chart_replay BLOCKED is correct.

## Corroborating cross-checks (not strictly my lens, but confirm the spine)
- Gate ordering: tool-payload-warning at t=47,982 ms; SENT tool-payload-confirmation at t=49,866 ms;
  `ordering.gate_before_dispatch=true`, `modflow_dispatch_rel_ms=null` — gate precedes dispatch. Consistent.
- The fix-proof chain (warning_id 01KTRKYYH68BXW9A8FXQR57DX2 -> accepted decision=proceed ->
  mf6 -> COG upload) is present in agent_log:126-135. No "unknown/closed" line. Consistent with the runner.
- GCS COG proof (gcs_plume_cog_proof.txt) reports 2856 bytes for plume_concentration_4326.tif; runtime
  unverifiable offline but the byte count and run_id (01KTRKZ1Q82R5HGPGH0QFZ96AC) match agent_log:128-131.

## Minor concern (already self-disclosed by runner — not a hidden contradiction)
- Scenario-A narration (`findings.json` A.narration) says "The resulting contaminant plume layer has been
  added to the map for your review" while publish_layer FAILED (agent_log:133) and final_map shows only
  basemap layers (in_idaho_bbox=false, CONUS view in A05). This is an optimistic/false render claim, but
  the NUMBERS the narration cites (Twin Falls Idaho, 2,946 mg/L, 0.013 km²) are all accurate and plausible.
  The runner flagged this explicitly as an honest-narration concern, so it is not a concealed defect.

## Conclusion
Every plausibility-testable claim holds; the BLOCKED items are genuinely unproduced (not faked); the
only false statement is the agent's own narration, which the runner already surfaced. PLAUSIBILITY: CONFIRM.
