# job-0255 — Adversarial verify (EVIDENCE INTEGRITY lens)

Verdict: **CONFIRM** the runner's FAIL. Severity: critical. Refute-by-default could not break any claim — every per-scenario verdict is exactly re-derivable from the raw artifacts.

## 1. pelicun_download = FAIL (NEITHER verbatim NOR repaired) — CONFIRMED
- agent_log_p5_turn.txt:170 — iter=11 `run_pelicun_damage_assessment` args:
  `hazard_raster_uri='https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=flood-depth-peak-01KTS8H8RJT6311A2V4BKX6H8A'`
  → a QGIS Server WMS GetMap URL. NOT a `gs://` COG; NOT a mangled `gs://`. (This is the OQ-62 LayerURI.uri the LLM copied from the flood result's loaded-layer entry — confirmed in uri_events.json loaded_layers[0].uri.)
- agent_log:197 → `_download_uri_to_local(hazard_raster_uri, ".tif")`.
- run_pelicun_damage_assessment.py:595-600 — `if not uri.startswith("gs://")` branch: `os.path.exists` is False for an https URL → raises `PelicunRuntimeError("local path does not exist: ...")`. agent_log:201 shows exactly this.
- Repair logic (run_pelicun_damage_assessment.py:624-644, "LLM path-mangle guard, job-0253") is reachable ONLY inside the `gs://` branch after `blob.download_to_filename` raises. An https input never enters that branch → guard cannot fire.
- `grep "path-mangle guard"` over the agent log = **0 occurrences**. Runner's "guard never fired (out of scope)" is literally true.

## 2. p5_impact = FAIL (no impact envelope, no ImpactPanel, Pelicun never executed) — CONFIRMED
- ws_frames.json: 48 frames total. Type histogram: pipeline-state 30, session-state 5, map-command 5, case-list 3, SENT:case-command 2, case-open 2, SENT:user-message 1. **No `impact-envelope` frame.** Full-blob substring scan: impact=0, chart=0, vega=0, impact-envelope=0, damage_state=0 (the 11 "pelicun" hits are the user-prompt text + the failed tool-card name).
- Pelicun pipeline-state lifecycle (frames 42→43→44): pending → running → **failed** (frame 44 state="failed", ts 2026-06-10T17:26:23.517Z). Matches agent_log:202 error at 10:26:23,518 (17:26:23 UTC). No success frame, no envelope follows.
- agent_log grep for impact-envelope / pelicun-success / pelicun-complete = **NONE**.
- Screenshot P02_no_impact_panel_flood_nsi_rendered.png: flood-depth + USACE NSI dots render on map; tool-card stack in chat; **no ImpactPanel slide-out**. ("ImpactPanel numbers match narration AND envelope frame" check is vacuously failed — there is no panel, no narration of numbers, and no envelope frame to reconcile.)

## 3. analysis_count / chart_emission / chart_replay = BLOCKED — CONFIRMED
- findings.json: `turns_sent: 1`; `fatal_error: "page.waitForTimeout: Target page, context or browser has been closed"` at stage3_p5_round10_job0255.mjs:433.
- agent_log:311 — gemini loop hit MAX_TURN_ITERATIONS=12 → loop_exhausted; then websocket 1001 (going away) on close (lines 312-337). Run terminated after the single P5 turn.
- No scenario-2/3/4 prompt ever sent; no chart frames exist (chart count = 0). Gating is correct; these cannot be PASS.

## Chart frames / replay integrity
- No chart-emission frames captured → nothing to validate for shape; replay scenario never reached. Consistent with BLOCKED, not a hidden PASS.

## Note (not a refutation, context)
The 12-turn loop was consumed by tool-routing thrash before Pelicun: fetch_usace_nsi out-of-allowed-set (iter 3,7), bad category 'data' (iter 5), duplicate flood model (iter 10 and again iter 12). Pelicun got exactly one attempt (iter 11) and failed on the URI. This is an agent-loop/URI-contract defect, not a harness-evidence defect — the testing evidence faithfully records a real FAIL.

## Conclusion
No artifact contradicts the runner's FAIL verdict. The single point worth flagging for the parent: the root cause is the LLM passing the WMS GetMap URL as `hazard_raster_uri` (the OQ-62 LayerURI surfaces the WMS URL, never the gs:// COG verbatim), and the job-0253 repair guard is structurally unable to help because it only repairs `gs://` inputs. Fixing the guard's scope (or resolving layer_id → gs:// COG before download) is the open work, not the evidence.
