# job-0244 adversarial re-derivation — EVIDENCE INTEGRITY lens

Verdict: **CONFIRM** the runner's PARTIAL. Every load-bearing claim re-derived from raw artifacts; no contradiction found.

## Critical orderings (all re-derived)
- **tool-payload-warning BEFORE MODFLOW dispatch**: gate emit `agent_log_round2.log:120` @04:16:27.815; first MODFLOW dispatch `:126 run_modflow_job spill=(42.555,-114.470)` @04:16:31.079 (+3.3s). `awk 'NR<120'` for any run_modflow_local/spill/mf6 line = none. ws_frames warning t_rel=47982ms, confirmation t_rel=49866ms — gate strictly precedes. gate_before_dispatch=true is honest.
- **"tool-payload-confirmation accepted" present; "unknown/closed" ABSENT**: `:121 tool-payload-confirmation accepted ... warning_id=01KTRKYYH68BXW9A8FXQR57DX2 decision=proceed`. `grep -i unknown/closed agent_log_round2.log` → NOT FOUND. warning_id identical across warning frame / SENT confirmation / server accept. THE FIX PROOF holds.
- **"uploaded plume COG to gs://"**: `:131 uploaded plume COG to gs://grace-2-hazard-prod-runs/01KTRKZ1Q82R5HGPGH0QFZ96AC/plume_concentration_4326.tif`; gcs_plume_cog_proof.txt = 2856 bytes, count 1. CONFIRMED.
- **code-exec-request before sandbox spawn**: NOT exercised (sandbox_gate_live BLOCKED, dead session). Correctly reported BLOCKED, not claimed.

## case2_plume FAIL = honest
- `:133 publish_layer failed ... cannot import name 'run_v2' from google.cloud`. A05_final_plume_map.png: CONUS-wide map; findings final_map center=(-95.5,37) zoom=4, layer_ids=[qgis-basemap, osm-fallback-basemap], in_idaho_bbox=false, plume.materialized=false. NOT Idaho, no plume overlay. FAIL is env/packaging, not the fix.

## Screenshot integrity notes (sloppy labels, NOT fabrication)
- md5(A02_gate_or_dispatch)==md5(A03_confirmation_gate) — same gate frame reused for two labels. Harmless (gate IS the confirmation).
- md5(A04_progress_45s)==md5(A05_final_plume_map)==md5(99_fatal) — three labels, one byte-identical post-narration terminal frame. "progress at 45s" overclaims a mid-run capture it isn't. Substantive content (gate card visible; CONUS map, no plume) is true in the shared image. Fix-proof rests on logs+ws_frames, not these PNGs, so this does not move the verdict.

## Honest-narration concern (corroborated)
findings.json A.narration: "The resulting contaminant plume layer has been added to the map" — FALSE given publish_layer failure. Runner flagged it. Matches feedback_synthetic_close_out_design.

## B/C BLOCKED = genuine
run_console_BC.log: B1 sig=false, frames frozen at 8, quietMs climbs to 455s. SCENARIO_B_loop_stall.md + log lines 160-178 show LANDCOVER_READ_FAILED (vsigs InvalidCredentials) then no iter=2. Genuinely blocked.

## Bottom line
PARTIAL is the correct, honest verdict. Fix proven live; failures correctly attributed to pre-existing env/packaging gaps + a secondary loop stall. Severity of integrity issues: minor (screenshot mislabeling only).
