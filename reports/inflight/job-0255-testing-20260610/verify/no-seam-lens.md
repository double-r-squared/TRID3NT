# job-0255 adversarial verify — NO-SEAM lens

Verdict: CONFIRM (runner FAIL is real; harness is legitimately live + no-seam).

## Lens scope
Verify the harness is free of `__grace2Inject*`, prompts are sent through the real UI,
Gemini latencies are real, and real Vertex calls fall in the test window. If any of these
fail, the runner's captured failure could be a seam artifact rather than a true product
failure. None failed.

## 1. Harness free of inject seams — CONFIRMED
- `web/tools/stage3_p5_round10_job0255.mjs` (33556 B, mtime 10:01) is the harness named in
  findings.fatal_error (`...stage3_p5_round10_job0255.mjs:433:20`).
- `grep __grace2` → only two hits: line 37 (comment "NO __grace2Inject* seams. Read-only
  __grace2GetMap permitted") and line 163 `window.__grace2GetMap?.()` (READ-ONLY map style
  introspection — permitted by audit "read-only __grace2GetMap OK").
- `grep -iE inject|window.__grace2(Inject|Set|Add|Render|Push)` → ZERO injection seams.
- No `exposeFunction` injecting envelopes; no synthetic frame fabrication.

## 2. Prompts driven through real UI — CONFIRMED
- sendPrompt() (lines 244-253): `page.locator('[data-testid="chat-input"]')` → `.click()`
  → `.fill(text)` → `.press("Enter")`. Real DOM chat input.
- ws_frames.json SENT frames: 1 `SENT:user-message` (t=9360ms) whose text matches the audit
  scenario verbatim ("Run a flood damage assessment for Fort Myers with Pelicun. Model the
  flood first with run_model_flood_scenario, then feed the flood-depth layer..."), plus 2
  `SENT:case-command` (create + select). turns_sent=1. No injected agent/tool frames.

## 3. Real Vertex calls in window — CONFIRMED
- agent_log_p5_turn.txt: 11 real `POST https://us-central1-aiplatform.googleapis.com/v1beta1/
  projects/grace-2-hazard-prod/locations/us-central1/publishers/google/models/
  gemini-2.5-pro:streamGenerateContent ... 200 OK` spanning 10:14:04 → 10:26:30.
- gemini usage lines show monotonic prompt growth (prompt=91294 → 92986) over a fixed cache
  prefix (cached=91105, hit=True) across iter 2-12 — the exact signature of a real multi-turn
  Vertex CachedContent session. A mock/inject could not produce this token accounting.

## 4. Real Gemini latencies — CONFIRMED
- Inter-turn gaps are real, not instant: e.g. iter9 fetch_usace_nsi response 10:15:39 →
  iter10 HTTP 10:15:47 (~8s); iter11 Pelicun error 10:26:23 → iter12 HTTP 10:26:30 (~7s).
- A genuine ~10-min SFINCS solver gap sits between iter10 (run_model_flood_scenario start
  10:15:48) and its completion 10:26:14, then iter11 fires. P01 progress screenshots span
  t0s→t1441s (real wall-clock ~24 min). turns_sent within the audit's <=6-turn budget? — the
  *Gemini turn iterations* hit MAX_TURN_ITERATIONS=12 internally; harness sent 1 user turn.
  rate_limited=False (no 429).

## Failure reality (cross-lens corroboration of runner's per_scenario)
- pelicun_download FAIL is REAL, not a seam artifact: iter11 the LIVE model emitted
  run_pelicun_damage_assessment with hazard_raster_uri = the QGIS Server WMS GetMap URL
  (`https://grace-2-qgis-server.../ogc/wms?...&LAYERS=flood-depth-peak-01KTS8H8...`), NOT the
  gs:// COG and NOT a mangled gs://. `_download_uri_to_local` (run_pelicun_damage_assessment.py:597)
  raised PelicunRuntimeError "local path does not exist". The path-mangle repair guard
  (commit 6804588) only handles gs:// suffix repair — an https WMS URL is out of its scope, so
  it correctly never fired. Pelicun invoked exactly once; no verbatim/repaired retry succeeded.
- p5_impact FAIL is REAL: zero `impact-envelope` frames in ws_frames.json; zero impact/
  ImpactEnvelope mentions in agent log; P02 screenshot shows flood-depth + USACE NSI rendered
  but NO ImpactPanel. The model then looped (re-ran flood at iter12) and the run was cancelled
  at MAX_TURN_ITERATIONS=12; browser closed → findings.fatal_error (page closed) is a
  post-run teardown artifact, not the cause of failure.
- analysis_count / chart_emission / chart_replay BLOCKED is correct — gated on p5_impact which
  never produced an envelope; harness sent only turn 1.

## Caveat (non-blocking, out of lens)
- No report.md exists in the job dir (task referenced "report.md"); STATE=READY_FOR_AUDIT,
  findings.json present. The runner's verdict was supplied inline. Does not affect the no-seam
  determination — all artifacts are internally consistent and the live drive is genuine.
