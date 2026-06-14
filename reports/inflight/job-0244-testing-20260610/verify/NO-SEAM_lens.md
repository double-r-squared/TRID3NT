# job-0244 adversarial verify — NO-SEAM lens

Verdict: CONFIRM (live-driven, no inject seams, real Vertex/Gemini round-trips).

## Lens scope
1. Harness scripts free of `__grace2Inject`.
2. Real Gemini latencies in WS logs.
3. Real Vertex calls in agent log during the session window.

## 1. No inject seams (CONFIRM)
- `grep __grace2Inject` over both harnesses returns ONLY two comment lines stating "NO __grace2Inject* seams". Zero functional usages.
- All `__grace2*` usages are read-only: `__grace2GetMap` (Map.tsx style/center read), `__grace2ActiveCaseId` (fallback case-id read). Read-only seams are explicitly permitted by the kickoff.
- Input is real: `web/tools/stage3_reverify_round2_job0244.mjs:244,246` and BC harness `:142,144` do `chatInput.fill(text)` + `press("Enter")` on `[data-testid="chat-input"]`. No envelope injection.
- Harness drives the REAL app: `chromium.launch` → `page.goto("http://localhost:5173")` (line 289/304). WS frames captured from the live socket via `page.on("websocket")` → `framereceived`/`framesent` (lines 41-99), not synthesized.

## 2. Real Gemini latencies in WS logs (CONFIRM)
- `ws_frames.json`: `gemini_generate` pipeline-state `running` at t=8554ms, first downstream tool (`list_categories`) at t=27706ms — a ~19s real round-trip. Tool calls staggered (27.7s, 33.5s, 38.7s) consistent with serial Gemini iterations. `tool-payload-warning` at t=47982ms; MODFLOW dispatch (`run_model_groundwater_contamination_scenario`) at t=49866ms — strictly AFTER warning + SENT confirmation. Terminal `gemini_generate complete` at t=66529ms. No instantaneous/zero-latency frames (which would signal injection).
- BC console log shows 470s of real wall-clock polling at ~15s cadence (genuine timer-driven wait, not scripted instant frames).

## 3. Real Vertex calls in agent log during session window (CONFIRM)
- Server boot: `model=gemini-2.5-pro project=grace-2-hazard-prod location=us-central1`.
- Scenario A session `01KTRKXGBTZJR37YMMM50YJ17F` (33 matches in agent log; identical to WS session_id — same live session):
  - `POST https://us-central1-aiplatform.googleapis.com/v1beta1/projects/grace-2-hazard-prod/locations/us-central1/publishers/google/models/gemini-2.5-pro:streamGenerateContent?alt=sse "HTTP/1.1 200 OK"` at lines 67, 79, 83, 110, 115, 138 — six genuine Vertex streaming POSTs.
  - `first-token ... elapsed_ms=14158.8` — realistic ~14s network first-token, not injected.
  - Six real Gemini iterations with token accounting (`cached=90811 total=92165 ... candidates=540`), CachedContent created against the live `cachedContents` endpoint (line 65).
- Scenario B session `01KTRMGNNFHT2GD042DVP6ZDC6`: another real Vertex POST (line 158), `first-token elapsed_ms=13729.9`, real iter=1 function-call. The vsigs `InvalidCredentials` and LANDCOVER_READ_FAILED are real GDAL errors from the live workflow — consistent with the env-gap BLOCKED claims, not fabricated.

## Fix-proof chain corroborated under this lens
Agent log lines 120-122: `solver-confirm gate emitted ... warning_id=01KTRKYYH68BXW9A8FXQR57DX2` → `tool-payload-confirmation accepted ... warning_id=01KTRKYYH68BXW9A8FXQR57DX2 decision=proceed` → `solver-confirm decision ... proceed`. No "unknown/closed" line anywhere (the round-1 failure mode). WS `SENT:tool-payload-confirmation` carries the IDENTICAL ULID. The accept happened on the live multi-WS-connection path.

## No contradictions found
- per_scenario claims (case2_gate PASS, case2_confirm_accepted PASS, case2_plume FAIL-env, narration PASS, p5/analysis/chart/sandbox BLOCKED) are all consistent with the live artifacts.
- The `publish_layer failed ... run_v2` and vsigs `InvalidCredentials` are honestly logged real failures, correctly attributed to env gaps rather than the fix.

## Severity
None for this lens — the runner's live-drive methodology is sound and verifiable. (The honest-narration concern the runner self-flagged is real but out of NO-SEAM scope.)
