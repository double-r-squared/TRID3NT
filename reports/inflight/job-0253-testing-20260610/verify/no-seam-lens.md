# job-0253 adversarial verify — NO-SEAM lens

Verdict: **CONFIRM** the runner's `verdict=FAIL`. No seam contamination; latencies and Vertex
calls are real and in-window. The harness honestly drove a live Gemini session; the FAIL is a
genuine product defect, not a harness artifact.

## Lens scope
NO-SEAM: harness free of `__grace2Inject*`; real Gemini latencies; real Vertex calls in window.

## 1. Harness free of inject seams — PASS
- `grep __grace2Inject` over BOTH `stage3_p5_round8_job0253.mjs` and `stage3_p5_round9_job0253.mjs`:
  only the comment line "NO __grace2Inject* seams. Read-only __grace2GetMap permitted."
- Only window hook used is read-only `window.__grace2GetMap?.()` (round9 L155) for map snapshot.
- All inputs are real UI: `chatInput.fill(text)` + `.press("Enter")` (L240/242), real button
  `.click()` for new-case / anon-auth / payload-proceed / chart / case-row. No synthetic WS send,
  no `dispatchEvent`, no `postMessage`, no fabricated frames. `framesent` handler only LOGS
  outbound user-message frames (read-only observation).

## 2. Real Gemini latencies — PASS
agent_restart_0253.log records genuine multi-second first-token latencies (incompatible with mock):
- session 01KTS5T50… (the P5 session) first-token elapsed_ms=**50558.6** (09:29:23), and a later
  turn elapsed_ms=23237.9 (09:47:43).
- earlier sessions: 26269.4, 22218.1 ms.
- usage metadata present every iter (`cached=91105 hit=True`, candidates token counts), session-
  scoped CachedContent created (cachedContents/3669603393229291520, tokens=91105, ttl 3600s).

## 3. Real Vertex calls in window — PASS
- 22 `streamGenerateContent ... "HTTP/1.1 200 OK"` POSTs to
  `us-central1-aiplatform.googleapis.com/.../models/gemini-2.5-pro:streamGenerateContent` in the log.
- The verified scenario session **01KTS5T50ET0FZZ1TWRMGCQTBA** aligns end-to-end:
  - 09:28:33 user-message "Run a flood damage assessment for Fort Myers with Pelicun…"
  - 09:28:59 new session-scoped cache created; 09:29:23 first-token (real 50.5s latency)
  - 09:38:38 postprocess_flood uploaded COG; publish_layer CONDITION_SUCCEEDED 09:42:46
  - 09:44:19 Pelicun iter=8 → 404; 09:44:58 Pelicun iter=9 → 404 — both in live browser window.
  - session_id in uri_events.json (`01KTS5T50ET0FZZ1TWRMGCQTBA`) matches the agent log.
- Agent started ONCE (08:55:07, single "starting agent server" line) — NO restart inside the
  P5/Pelicun window. PID-stable per kickoff "DO NOT RESTART".

## Independent corroboration of the FAIL claim (cross-lens, from artifacts)
The agent log is the authoritative URI source (WS-side `analyzeUriDiscipline` returned empty
because the browser closed at the FATAL before the Pelicun tool-call-start was observed on WS — a
harness telemetry gap, NOT a fabrication):
- run_model_flood_scenario actually published COG (log L407):
  `gs://grace-2-hazard-prod-runs/01KTS5W9GTE7A7WPC3BNBE10EQ/flood_depth_peak.tif`  (NO `runs/` seg)
- Pelicun iter=8 AND iter=9 were fed (log L475/L557):
  `gs://grace-2-hazard-prod-runs/runs/01KTS5W9GTE7A7WPC3BNBE10EQ/flood_depth_peak.tif`  (extra `runs/`)
- → Gemini MANGLED the URI (correct bucket + correct run_id, spurious `runs/` segment inserted).
  Verbatim-copy contract NOT satisfied. Both calls 404 (real GCS NotFound), 0 ImpactEnvelopes,
  ImpactPanel absent (P02_no_impact_panel.png). Matches blocker OQ-0253-PELICUN-URI-RUNS-PREFIX-
  MANGLE and the runner's per-scenario verdicts exactly. Cache-path hallucination IS gone
  (it is the runs bucket, not `-cache/cache/`), so the d534f4c fix moved the failure mode but did
  not close it.

## Caveat (does not change verdict)
The round-9 harness ended in a FATAL ("Target page… closed" at L425) rather than a clean settle —
the browser closed during the post-clarification wait, and the WS-side uri capture missed the
Pelicun call. The FAIL stands regardless because the agent-side log (independent of the browser)
records the mangled URI + double 404 unambiguously. This is a harness-robustness note for the next
round, not seam contamination.
