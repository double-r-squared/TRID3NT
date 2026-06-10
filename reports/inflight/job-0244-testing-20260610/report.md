# job-0244 report — Stage 3 re-verify ROUND 2 (testing specialist)

**Executed:** 2026-06-10, live one-session re-verify of the job-0243 confirmation-registry fix (commit `768454a`), on top of the job-0241 gate (commit `e712ca6`).
**Agent under test:** PID 3036624 on :8765, 89 tools, gemini-2.5-pro, env `GRACE2_MODFLOW_LOCAL=1` / `GRACE2_MF6_BIN=/tmp/mf6` (mf6 6.5.0). NOT restarted (as instructed).
**Overall verdict: PARTIAL.** The **job-0243 fix is DECISIVELY PROVEN** on the live path: the Proceed click that round-1 dropped as "unknown/closed warning_id" now resolves via the session-scoped registry — `tool-payload-confirmation accepted ... decision=proceed` — the gated MODFLOW solver runs, mf6 converges, and the plume COG is uploaded to GCS. The round-1 blocker is gone. The overall PASS gate also requires `case2_plume` = "plume renders ON THE MAP", which FAILS for THREE pre-existing local-environment gaps (not the fix). Scenarios B/C are BLOCKED by those env gaps + a secondary loop-stall.

Gemini turns used: ~3 (A: gate + post-Proceed terminal; B1: one in-flight call that returned a degraded flood envelope). No 429s.

---

## Per-scenario verdicts

| sub-scenario | verdict | evidence |
|---|---|---|
| **case2_gate** | **PASS** | `tool-payload-warning` at +49.1s BEFORE any MODFLOW dispatch (`gate_before_dispatch=true`, `modflow_dispatch_rel_ms=null`); tool `run_modflow_job`; inline card w/ Proceed/Cancel. `evidence/A03_confirmation_gate.png`, `findings.json` A.ordering, agent log `solver-confirm gate emitted ... location='Twin Falls, Idaho'`. |
| **case2_confirm_accepted** | **PASS — THE FIX PROOF** | Web SENT `warning_id=01KTRKYYH68BXW9A8FXQR57DX2 decision=proceed`; agent log `tool-payload-confirmation accepted session=... warning_id=01KTRKYYH68BXW9A8FXQR57DX2 decision=proceed` -> `solver-confirm decision ... proceed` -> MODFLOW dispatch. NO "unknown/closed" line (the round-1 failure). `evidence/ws_frames.json`, `evidence/agent_log_round2.log` lines 120-126. |
| **case2_plume** | **FAIL (env gap, not the fix)** | mf6 ran local (`mf6 exit=0 converged=True`), postprocess `max_concentration_mgl=2946.32 plume_area_km2=0.0125`, `uploaded plume COG to gs://...plume_concentration_4326.tif` (verified in GCS, 2856 bytes — `evidence/gcs_plume_cog_proof.txt`). BUT `publish_layer failed ... google-cloud-run not importable`. COG never registered as WMS layer -> no map overlay (`plume.materialized=false`; map CONUS-wide — `evidence/A05_final_plume_map.png`). |
| _(narration)_ | **PASS** | "...near Twin Falls, Idaho... peak contaminant concentration of approximately 2,946 mg/L and a plume area of about 0.013 km²." `findings.json` A.narration (idaho/conc/area all True); `A05_final_plume_map.png`. NB: narration ALSO claims "plume layer has been added to the map" — false given the publish failure (honest-narration concern). |
| **p5_impact** | **BLOCKED** | Fresh Fort Myers flood scenario failed at SFINCS deck-build: GDAL `/vsigs/` -> `InvalidCredentials` (GOOGLE_APPLICATION_CREDENTIALS unset) -> `LANDCOVER_READ_FAILED`. Then the agent loop stalled (iter=2 never fired, 0% CPU >210s). No flood layer -> no Pelicun -> no ImpactPanel. `evidence/SCENARIO_B_loop_stall.md`, `evidence/agent_log_round2.log` lines 160-178. |
| **analysis_count** | **BLOCKED** | Depends on B1 impact result (never produced). |
| **chart_emission** | **BLOCKED** | Depends on B1. |
| **chart_replay** | **BLOCKED** | Depends on B1. |
| **sandbox_gate_live** | **BLOCKED** | B+C harness stopped after the B1 stall (dead session). The cloud sandbox path would also hit the run_v2 gap; GRACE2_SANDBOX_LOCAL is unset. Not exercised live this round. |

**Overall PASS gate (case2_gate + case2_confirm_accepted + case2_plume): NOT met** — case2_plume's map-render leg fails on an env gap. The fix itself is proven; the demo cannot render E2E on this machine.

---

## The fix proof — full chain of custody (case2_confirm_accepted)
Round-1: gate EMITS but Proceed dropped (per-CONNECTION `pending_payload_warnings` vs multiple WS connections -> "unknown/closed warning_id"). job-0243: module-level `_PENDING_CONFIRMATIONS` keyed on the ULID warning_id, owner-session-tagged. Round-2 live confirms:
1. `tool-payload-warning` frame: warning_id `01KTRKYYH68BXW9A8FXQR57DX2`, `run_modflow_job`, args (TCE, Twin Falls Idaho, 42.556/-114.470, 3.07 kg/s, K 1e-4, phi 0.3).
2. Web SENT `tool-payload-confirmation` with the IDENTICAL warning_id, decision=proceed.
3. Agent: `accepted ... decision=proceed` -> `solver-confirm decision ... proceed` -> `run_modflow_job local=True` -> `mf6 exit=0 converged=True` -> `postprocess_modflow max_concentration_mgl=2946.32` -> `uploaded plume COG to gs://`.
This is exactly the resume leg severed in round-1. **The fix works end-to-end on the live multi-WS-connection path.**

---

## Environment gaps surfaced (NOT regressions in the fix; masked in round-1)
- **OQ-0244-PUBLISH-NO-RUN-V2** (blocks all map publish): `publish_layer` dispatches the PyQGIS worker via `google.cloud.run_v2.JobsClient.run_job`, but `google-cloud-run` is NOT installed (not in `services/agent/pyproject.toml`; absent from the `google.cloud` namespace) and `set_jobs_client(...)` isn't called at startup. Every NEW agent layer publish fails (`JOBS_CLIENT_UNAVAILABLE`). Pre-baked flood-depth layers from prior machines still serve from QGIS Server (verified via GetCapabilities). Owner: infra/agent.
- **OQ-0244-VSIGS-NO-CREDS** (blocks fresh flood scenario): SFINCS deck-build reads landcover via GDAL `rasterio.open('/vsigs/...')` -> `InvalidCredentials` because `GOOGLE_APPLICATION_CREDENTIALS` is unset (ADC works for the Python storage client — that's why MODFLOW's upload succeeded — GDAL's vsigs driver needs the env var). Owner: infra.
- **OQ-0244-SANDBOX-NO-RUN-V2** (blocks live sandbox exec): cloud sandbox path lazy-imports `run_v2` -> same ImportError, surfaced cleanly as `status=error`. Gate REQUEST + Proceed (the fix) should work; EXECUTION can't return `status=ok` here. Owner: infra (install google-cloud-run, or set GRACE2_SANDBOX_LOCAL=1 for parity with mf6).
- **OQ-0244-LOOP-STALL-ON-DEGRADED-ENVELOPE** (secondary, agent): after `function-response queued` for the failed flood scenario, the loop did not issue iter=2 (no narration, no retry, 0% CPU >210s). Downstream of the vsigs gap, but worth an agent look — job-0177 retry / always-narrate should have fired. Owner: agent.

## Honest-narration concern
Scenario-A narration claims "plume layer has been added to the map" while `publish_layer` had just FAILED. The always-narrate clause produces optimistic narration that contradicts the tool outcome — exactly what `feedback_synthetic_close_out_design` flags. Needs a function_response classifier.

## Harness notes
- A+B+C driver: `web/tools/stage3_reverify_round2_job0244.mjs` (live, no inject seams; read-only `__grace2GetMap`). Recorded all Scenario-A evidence, then crashed at Scenario-B `newCase()`: `grace2-cases-new` lives in cases-ROOT but after Scenario A we were inside `grace2-case-view`.
- B+C driver (nav fix): `web/tools/stage3_reverify_round2_BC_job0244.mjs` — `newCase()` first returns to cases-root via `grace2-case-view-cases-link`. Proven working (`evidence/B01_new_case.png` shows the fresh Fort Myers case reached). B1 then stalled on the env gap (above); harness stopped to avoid burning the 900s timeout on a dead session.

## Carry-overs / open questions
- The four OQ-0244-* items must be closed before Case 2 plume / fresh flood / live sandbox render E2E on this machine. Three are infra/env; one is an agent loop-resilience follow-up.
- **OQ-0242-WS-STATE-SPLIT: RESOLVED** by job-0243 — proven live this round.
- 0236 MRMS/SFINCS legs remain PARTIAL pending a live CONUS flood warning (unchanged).
