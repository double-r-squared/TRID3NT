# job-0248 ADVERSARIAL VERIFY — PLAUSIBILITY lens (REFUTE-by-default)

Verdict: **CONFIRM** (severity: none) — every artifact that exists is geographically/numerically
plausible; the items my lens would scrutinize hardest (Fort Myers building counts, damage
distribution, Vega-Lite) genuinely do NOT exist and are honestly reported as BLOCKED, not fabricated.

## Lens checks (instructed)

### 1. Idaho bbox for the plume — PLAUSIBLE (verified independently)
- `scenarioR_render_proof.json` plume bbox EPSG:4326 = W -114.4826 / E -114.457661 / S 42.546548 / N 42.56512.
- Center = (42.5558, -114.4701) — essentially EXACT Twin Falls, ID centroid (42.5558, -114.4701).
  Distance to reference ~0.0 km. Footprint ~2.04 km W-E x 2.06 km N-S — sane for a localized plume.
- Within Idaho state bbox (lon [-117.24,-111.04], lat [41.99,49.0]): TRUE.
- Visual: `R_getmap_twinfalls_context.png` shows OSM labels "Twin Falls", "College of Southern
  Idaho", US-30/US-93 — unambiguously Twin Falls, Idaho. No contradiction.

### 2. Sandbox numbers — CORRECT
- numpy([1,5,9,12]): mean = 6.75, max = 12. Reproduced exactly. `findings.json` C.has_mean_675=true,
  has_max_12=true; `code-exec-result` frame value "{'mean': np.float64(6.75), 'max': np.int64(12)}".
- WS ordering req(idx65) < res(idx70): true. Plausible and correct.

### 3. Fort Myers counts / damage distribution — DO NOT EXIST (cannot be implausible)
- Scenario B is BLOCKED at the flood build: `build_sfincs_model raised HYDROMT_BUILD_FAILED`
  (DEM /vsigs read "No such file found") on BOTH the initial turn and the retry
  (`scenarioB_run2_flood_blocker.log` 06:26:41 + 06:28:44).
- No flood depth layer -> compute_impact_envelope rejected (OutOfAllowedSet, not in hot-set) ->
  fetch_usace_nsi rejected -> no Pelicun, no impact envelope, no ImpactPanel.
- `ws_frames.json` frame-type census: NO `impact-envelope` / impact-result frame type exists.
  The only "impact" string hits are (a) the user prompt ("Show me the impact summary"),
  (b) agent narration chunks, (c) pipeline-state frames referencing the *rejected*
  compute_impact_envelope call. No emitted counts.
- `B03_no_impact_panel.png`: shows correct Fort Myers / Caloosahatchee River geography, the
  run_model_flood_scenario card, agent narration — and NO ImpactPanel. Consistent with BLOCKED.
- => There are no building counts and no damage distribution to assess for order-of-magnitude
  sanity. The runner did NOT fabricate any. This is the honest, plausible state.

### 4. Vega-Lite validity — N/A (chart emission BLOCKED, depends on B1 impact)
- B3 (chart_emission) and B4 (chart_replay) skipped because B1 produced no impact. No Vega-Lite
  spec was emitted. Honestly reported; nothing to contradict.

### Routing-geography sanity (supporting)
- Fort Myers fetched bbox [-81.9126, 26.5476, -81.7511, 26.6892] contains the real city centroid
  (26.6406, -81.8723): TRUE. B1 UI map center (26.6184, -81.8319), zoom 11.83 — SW FL coast. Sane.

## Conclusion
From the PLAUSIBILITY lens there is no contradiction. The PASS scenarios (R render in correct
Idaho/Twin Falls location; C sandbox real numbers 6.75/12) are geographically and numerically
sound. The BLOCKED scenarios (B1 P5, B2 analysis, B3/B4 charts) genuinely produced no numbers,
and the runner reports them as BLOCKED rather than inventing plausible-looking counts — which is
exactly what an honest report should do. CONFIRM the runner's PARTIAL verdict.
