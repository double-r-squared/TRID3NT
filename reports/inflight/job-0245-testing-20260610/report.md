# job-0245 report — Stage 3 re-verify ROUND 3 (closing session, testing specialist)

**Executed:** 2026-06-10, single live browser session, after the job-0244 env fixes (commit `fdf9b6d`).
**Agent under test:** PID 3052144 on :8765, 89 tools, gemini-2.5-pro. NOT restarted (as instructed).
Env confirmed live on the agent process: `GOOGLE_APPLICATION_CREDENTIALS=/home/nate/.config/gcloud/application_default_credentials.json`, `GRACE2_SANDBOX_LOCAL=1`, `GRACE2_MODFLOW_LOCAL=1`, `GRACE2_MF6_BIN=/tmp/mf6`, `GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod`, `GOOGLE_GENAI_USE_VERTEXAI=True`. `google.cloud.run_v2.JobsClient()` import + construction verified in the agent venv.
**Harness:** `web/tools/stage3_reverify_round3_job0245.mjs` — LIVE, NO inject seams (read-only `__grace2GetMap` only). A→B→C in one session, BC nav-fix folded in, B1 stall-guard, fresh-case Scenario C. Gemini turns used: **3** (A: TCE article; B1: Fort Myers flood; C: numpy sandbox). No 429s.

**Overall verdict: PARTIAL.**

The round-2 blocker `OQ-0244-PUBLISH-NO-RUN-V2` is **CLOSED**: `publish_layer` now dispatches the Cloud Run PyQGIS worker (`run_v2`) end-to-end, the worker exits `CONDITION_SUCCEEDED`, and it writes the plume layer into the canonical served `.qgs` (Gemini-free GCS proof below). But the round-3 PASS-gate legs both fail/block:
- **case2_render FAILS** on a newly-isolated infra gap: QGIS Server never invalidates its in-memory project cache for the gcsfuse-mounted `.qgs`, so the freshly-published layer is not served (`LayerNotDefined`) → no MapLibre overlay.
- **sandbox_gate_live BLOCKED**: the agent mis-routed the Python prompt (and the Fort Myers flood prompt) to the prior turn's Twin-Falls groundwater composer — a severe, reproducible context-carryover in the reused WS session. No SandboxCard was ever emitted.

Overall PASS gate (case2_render PASS + sandbox_gate_live PASS): **NOT met**.

---

## Per-scenario verdicts

| sub-scenario | verdict | one-line evidence |
|---|---|---|
| **case2_render** | **FAIL (infra: QGIS project cache)** | publish PROVEN end-to-end (worker CONDITION_SUCCEEDED + layer in served .qgs), but QGIS Server serves a stale cached project → `LayerNotDefined`, no overlay (`plume.materialized=false`) |
| **p5_impact** | **BLOCKED (agent context-carryover mis-route)** | Fort Myers flood prompt routed to Twin-Falls groundwater gate; gate unanswered → gate-timeout cancellation; no flood, no Pelicun, no ImpactPanel |
| **analysis_count** | **BLOCKED** | depends on B1 impact (never produced) |
| **chart_emission** | **BLOCKED** | depends on B1 |
| **chart_replay** | **BLOCKED** | depends on B1 |
| **sandbox_gate_live** | **BLOCKED (same context-carryover mis-route)** | numpy prompt routed to groundwater gate (twice); NO SandboxCard, NO code_exec_request frame; local sandbox path never exercised |

---

## Scenario A — Case 2 render proof (the headline)

- **Gate-before-dispatch (regression): PASS.** `findings.json` A.ordering: `gate_seen_rel_ms=44440`, `modflow_dispatch_rel_ms=null`, `gate_before_dispatch=true`. `evidence/A03_confirmation_gate.png`.
- **Confirmation accepted (job-0243 registry fix, regression): PASS.** `tool-payload-confirmation accepted session=01KTRNN09HZ5BWVQXE5D9C9Y3Q warning_id=01KTRNPAEBSE38AWFETJ3F5W1J decision=proceed` → `solver-confirm decision ... proceed`.
- **MODFLOW solve + COG upload: PASS.** `run_modflow_local mf6 exit=0 converged=True` → `postprocess_modflow max_concentration_mgl=2946.32 plume_area_km2=0.0125` → `uploaded plume COG to gs://...runs/01KTRNPCV4.../plume_concentration_4326.tif`.
- **publish_layer worker dispatch — `OQ-0244-PUBLISH-NO-RUN-V2` CLOSED: PASS.** (`evidence/scenarioA_agent_chain.log`)
  - `publish_layer: dispatching Cloud Run Job .../jobs/grace-2-pyqgis-worker env_overrides={WORKER_OP: publish-raster, RASTER_LAYER_ID: plume-concentration-01KTRNPCV4...}` (04:46:48)
  - `publish_layer: execution completed state=CONDITION_SUCCEEDED layer_id=plume-concentration-01KTRNPCV4... wms_url=https://grace-2-qgis-server-.../ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=plume-concentration-01KTRNPCV4...` (04:49:48, ~3min worker cold start)
  - `publish_layer succeeded` → `run_modflow_job complete ... uri=<WMS URL>` → `case2 complete location='Twin Falls, Idaho'` → `function-response queued iter=5 ... summary_keys=['result','status','tool']` (success, not error).
- **Worker persisted the layer (Gemini-free GCS proof, `evidence/qgs_layer_proof.txt`):** canonical `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` updated 2026-06-10 11:49:34Z (generation 1781092174312777), `has round3 layer: True`, maplayer count 11.
- **Map render: FAIL.** `A.plume.materialized=false`; `A.final_map.center={-95.5,37} zoom=4 layer_ids=[qgis-basemap, osm-fallback-basemap] in_idaho_bbox=false`; layer panel empty. `evidence/A05_final_plume_map.png` (CONUS, no overlay). QGIS Server (`evidence/getmap_layernotdefined.xml`): `GetMap LAYERS=plume-concentration-01KTRNPCV4...` → HTTP 400 `LayerNotDefined` (6/6 attempts); `GetCapabilities` lists only the older `plume_smoke_job0244`, which still serves HTTP 200 image/png (server is healthy, project cache stale).
- **Honest-narration concern (persists, now end-user-facing):** terminal narration says *"The plume layer has been added to the map for visualization."* (`A.narration.claims_map_add=true`) — honest vs the tool's success result, but the user sees no layer. `feedback_synthetic_close_out_design` territory; a classifier distinguishing "published into project file" from "renderable now" would help.

---

## Scenario B — analysis + P5 (BLOCKED, new cause)

The Fort Myers B1 prompt routed NOT to a flood scenario but AGAIN to `run_model_groundwater_contamination_scenario` for **Twin Falls, Idaho** (geocode cache-hit on the prior turn), emitting a fresh solver-confirm gate (`warning_id=01KTRP92D7AFR5YDZGBTE06NB7`, trichloroethylene) at 04:56:56 — a **context-carryover mis-route** from Scenario A's TCE turn (same browser/WS session `01KTRNN09...`, 90,811 cached prompt tokens). The harness B1 path does NOT click a mid-turn gate, so it sat unanswered; at 05:01:56 the gate hit its TTL → `SolverConfirmationCancelledError: ...declined at the parameter-confirmation gate (user chose 'cancel' or the gate timed out); the solver did not run` → loop terminal iter=2 with an honest cancellation narration. No flood → no SFINCS → no Pelicun → no ImpactPanel (`B.impact_panel_present=false`, `b23_skipped=true reason=no_impact_panel`). DISTINCT from round-2's silent loop-stall (the gate now times out and narrates honestly — a resilience improvement). The vsigs fix was NEVER exercised because SFINCS was never reached.

---

## Scenario C — sandbox live gate (BLOCKED, same context-carryover)

Run in a FRESH case (`C00_new_case.png`). The prompt was received correctly (`user-message ... text='Run a quick Python computation: compute the mean and max of the flood depth rast...'`, 05:13:48), but the agent's first action was AGAIN `geocode_location query='Twin Falls, Idaho'` + `solver-confirm gate emitted ... tool=run_model_groundwater_contamination_scenario warning_id=01KTRQ8ESSXTTKPHVP7KBC4WKX contaminant='trichloroethylene'` (05:14:05) — NOT a `code_exec_request`/SandboxCard. `findings.json` C: `sandbox_request_present=false`, `code_exec_request_frame=false`. `evidence/C01_no_sandbox.png` shows TWO "Large response expected" `run_modflow_job` (groundwater/Twin Falls) confirmation cards in chat — no SandboxCard. The local-sandbox gate was never reached this round.

(Note: a leftover round-2-era driver session DID exercise the local sandbox earlier — `sandbox local run: .../python .../infra/python-sandbox/executor.py (cap=60s)` → `code-exec-result emitted status=error` — but that was not this round's live-driven turn and is not counted as a PASS.)

---

## Open questions / gaps surfaced (round-3)

- **OQ-0244-PUBLISH-NO-RUN-V2: CLOSED.** google-cloud-run installed; `publish_layer` dispatches the worker via `run_v2`; worker exits `CONDITION_SUCCEEDED`; layer persisted into the served `.qgs`. Proven this round.
- **OQ-0245-QGIS-PROJECT-CACHE (NEW, infra — blocks ALL fresh-layer map render):** QGIS Server keeps its parsed project cached per FCGI worker and never reloads it for the gcsfuse-mounted `gs://.../grace2-sample.qgs`. `infra/qgis-server/Dockerfile` sets `QGIS_SERVER_CACHE_DIRECTORY`/`QGIS_SERVER_CACHE_SIZE` but NO project-cache invalidation (`QGIS_SERVER_PROJECT_CACHE_STRATEGY`/`_CHECK_INTERVAL` unset; gcsfuse mtime/inotify semantics don't drive the filesystem watcher). A layer published into the GCS `.qgs` is invisible to already-running workers until a cold instance re-parses. `evidence/qgis_server_cache_config.txt`, `evidence/getmap_layernotdefined.xml`, `evidence/qgs_layer_proof.txt`. Owner: infra. Fix candidates: `QGIS_SERVER_PROJECT_CACHE_STRATEGY=periodic` + a short `_CHECK_INTERVAL`; OR `publish_layer` busts the cache (project-reload signal / unique-path .qgs per publish / MAP cache-bust); OR cycle the server revision after publish.
- **OQ-0245-CONTEXT-CARRYOVER-MISROUTE (NEW, agent — blocks every fresh-topic turn in a reused WS session):** in a reused anonymous WS session, NEW prompts (Fort Myers flood; numpy sandbox) both routed to the PRIOR turn's Twin-Falls groundwater composer. New-case isolation resets case state (`case-open ... chat=0 layers=0`) but NOT the LLM conversation context on the same WS connection; the ~90.8k-token cached prompt prefix containing the TCE turn dominates routing. Reproduced on 2 consecutive turns (B1 + C). Owner: agent. This is the single biggest blocker to multi-scenario E2E demos in one session.
- **OQ-0244-VSIGS-NO-CREDS: NOT RE-EXERCISED.** B1 never reached SFINCS deck-build (mis-route). Carry-over.
- **Honest-narration (carry-over):** always-narrate emits a "layer added to the map" claim that the UI contradicts when QGIS cache staleness suppresses the overlay.

## Harness notes
- `web/tools/stage3_reverify_round3_job0245.mjs`; evidence in `reports/inflight/job-0245-testing-20260610/evidence/` (findings.json, ws_frames.json, scenarioA_agent_chain.log, qgs_layer_proof.txt, qgis_server_cache_config.txt, getmap_layernotdefined.xml, A0*/B0*/C0* screenshots).
- Harness heuristic note: the B1 settle check keys off the last pipeline-state frame for `generating`; when the agent halts on a gate the last frame can read gemini_generate, so the B1 loop ran to its 900s budget before advancing. The verdict is unaffected (no impact panel either way).
