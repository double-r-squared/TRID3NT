# job-0248 adversarial verify — EVIDENCE INTEGRITY lens

Re-derived from raw artifacts (images opened, WS frames parsed, logs read). REFUTE-by-default.

## VERDICT: CONFIRM (runner verdict=PARTIAL is honest and artifact-backed)

The runner's PARTIAL is the correct call. Every claim it makes either renders/parses as
described, or is honestly labeled BLOCKED with the right (env/infra) attribution. No fabricated
success on a real failure.

## R — case2_render_zero_gemini: CONFIRM (PASS)
- `R_getmap_plume_tight.png` (opened): real non-transparent plume cells (1 black + grey cells)
  on transparent ground — matches render-proof `nonzero_alpha_px=340 / 262144`.
- `R_getmap_plume_over_basemap.png` (opened): Twin Falls OSM street grid + plume cell overlaid.
- `scenarioR_getmap_url.txt`: GetMap HTTP 200, image/png, LAYERS=plume-concentration-
  01KTRNPCV4NEN0RRQ3H0QMZQY6 against canonical grace2-sample.qgs.
- Idaho check re-derived: plume centroid (-114.470, 42.556) is Twin Falls, ID; within Idaho
  bbox. CONFIRMED independently.
- Zero Gemini: render is a pure WMS GetMap HTTP fetch; no agent loop, no user-message frame in
  the R path. CONFIRMED.
- Rehydration empty (loaded_layer_summaries=[]) is honestly disclosed and matches the kickoff's
  anticipated fallback; not hidden. The case record predates the round-3 publish into the
  canonical .qgs. Honest.

## C — sandbox_gate_live: CONFIRM (PASS) — strongest evidence in the job
- `C01_sandbox_request.png` (cropped+opened): "Python sandbox — confirm execution" card showing
  verbatim `import numpy / my_array = numpy.array([1, 5, 9, 12]) / result = {'mean': ...}` with
  Proceed/Cancel — the GATE, BEFORE execution.
- `C02_sandbox_result.png` (cropped+opened): "Python sandbox result" card, ok chip, same code,
  RESULT `"{'mean': np.float64(6.75), 'max': np.int64(12)}"`; narration "mean of 6.75 and a max
  of 12". REAL numbers, not placeholders.
- ws_frames.json strict ordering (re-parsed):
  - frame 65 code-exec-request (t=420827ms) code_exec_id=01KTRVQ7NRR20ZBWVXNXYPBM97, verbatim code
  - frame 66 SENT:tool-payload-confirmation (t=421610ms) warning_id=...PBM97 decision=proceed
  - frame 70 code-exec-result (t=422178ms) code_exec_id=...PBM97 status=ok value mean 6.75/max 12
  - IDs match across all three; request<confirm<result by timestamp. Gate intact. CONFIRMED.
- `sandbox_local_gemini_free_proof.txt`: local executor returns mean 6.75 / max 12.0 — corroborates
  the executor is Gemini-free.

## B1 p5_impact: CONFIRM-as-BLOCKED (correct attribution)
- agent log (scenarioB_run2_flood_blocker.log): build_sfincs_model raised HYDROMT_BUILD_FAILED,
  underlying "No such file found: /vsigs/...dem...87ba00463af0275d02115f7463afe6e9.tif".
- Object verified PRESENT in GCS (1.84 MiB) per blocker note => env/infra (local GDAL /vsigs auth),
  NOT cache expiry, NOT agent logic. Routing correct: run_model_flood_scenario dispatched directly,
  gw_hit_count=0 (no Twin-Falls/groundwater regression), flood_route_hit_count=7.
- `B03_no_impact_panel.png` (opened): no ImpactPanel, no flood overlay — consistent with no layer.
  BLOCKED label is honest. ImpactPanel-vs-narration: no ImpactPanel to contradict.
- B2/B3/B4 (analysis_count, chart_emission, chart_replay): legitimately skipped — depend on B1's
  impact layer which never existed. No chart artifacts exist to verify fidelity (b23_skipped=true).

## DISCREPANCIES (minor, do not change verdict)
1. METHODOLOGY: Scenarios B and C share ONE WS session_id (01KTRVADP20JR8VGZ9700T9VM5). C opens a
   new CASE (case-command frames 52/56) but reuses the session/connection, so kickoff's "FRESH
   Case (~2 turns)" for C is partially met (new case, same session). The sandbox gate evidence is
   unaffected — frames 65/70 are real and ID-consistent.
2. CONTEXT BLEED: In scenario C the carried Fort Myers flood prompt context caused a
   run_model_flood_scenario call before the numpy gate (c_scroll_tail + frame 59-64). The numpy
   sandbox path is independent and clean; this is cosmetic.
3. NARRATION OPTIMISM (already self-disclosed, OQ-0248-FLOOD-NARRATION-OPTIMISM): agent narrated
   "I have initiated the flood scenario ... Please wait" after run_model_flood_scenario returned a
   FAILURE envelope. Routing correct, narration overstated. Low severity, honestly logged.

## Integrity conclusion
No artifact contradicts the runner's per-scenario claims. The two PASS scenarios (R render, C
sandbox) are backed by openable images + ID-consistent WS frames + real numbers. The BLOCKED
scenarios are correctly attributed to an env/infra /vsigs auth gap, with the underlying GCS object
proven present. The runner did NOT manufacture a green where the system failed.
