# job-0248 — Adversarial verify, NO-SEAM lens

Verdict: **CONFIRM** (runner PARTIAL is sound; NO-SEAM dimension fully passes). Severity: none.

## Lens questions, each answered from artifacts

### 1. Harnesses free of __grace2Inject
- Harness used: `web/tools/stage3_reverify_round5_job0248.mjs` (609 lines).
- `grep __grace2Inject` → only hit is line 22, the prohibition comment:
  `// LIVE-DRIVEN ONLY: NO __grace2Inject* seams. Read-only __grace2GetMap permitted.`
- All `__grace2` refs in harness: `__grace2GetMap` (lines 10,114) + the line-22 comment. No write/inject seam.
- Prompt delivery is REAL chat input: `chatInput.fill(text)` + `chatInput.press("Enter")` on `[data-testid="chat-input"]` (lines 207-211). No envelope injection.
- Scenario R sends NO prompt at all — only `gotoCasesRoot`, read-only deeplink `page.goto(/?case=...)` (annotated "read-only navigation, NOT an inject seam", line 290), row clicks, layer/map snapshot, and out-of-band WMS GetMap. Zero Gemini by construction.
- No __grace2Inject anywhere in the job evidence dir either (only `__grace2GetMap` in audit.md).

### 2. Real Vertex calls in the window
- Live agent log `/tmp/agent_restart_0247.log` (agent PID 3091123, started 06:09:59, port 8765, model=gemini-2.5-pro, project=grace-2-hazard-prod, location=us-central1).
- Every Gemini turn is preceded by a real httpx POST to
  `https://us-central1-aiplatform.googleapis.com/v1beta1/projects/grace-2-hazard-prod/locations/us-central1/publishers/google/models/gemini-2.5-pro:streamGenerateContent?alt=sse "HTTP/1.1 200 OK"`.
- No mock/stub toggle in `services/agent/src` (grep for MOCK_GEMINI/STUB_GEMINI/fake_gemini → empty).

### 3. Real Gemini latencies (not stubbed)
- first-token elapsed_ms genuinely varied across turns: 20996.8, 29642.6, 15751.4, 34784.8 ms. Stubs do not produce 15–35s variable network round-trips.

### 4. Zero-Gemini scenario R genuinely made NO generate calls
- DECISIVE: In the entire window from agent restart (06:09:59) to the FIRST Vertex streamGenerateContent call (06:20:15 — which is run1's Scenario B session 01KTRTZ90Z170R6TQKZJ9DCT31), there is ZERO Gemini activity. `awk` over 06:10:00–06:20:14 for aiplatform|gemini function-call|usage|first-token|generate_content → empty.
- Only agent activity in the R window: auth_handshake(anonymous), auth-ack x4, case-list x2 (UI rehydration), tool_catalog_http x1. No user-message, no streamGenerateContent.
- Two harness runs reconciled: run1 (partial, crashed at line 367 "Target page closed", turns_sent=1, B session 01KTRTZ…) and run2 (final harness_run.log, turns_sent=3, B+C session 01KTRVADP20JR8VGZ9700T9VM5 first Gemini 06:26:28). In BOTH runs Scenario R precedes any Gemini call. gemini_turns=0 for R is true.

### Render proof independently reproduced
- Re-fetched the documented GetMap URL: HTTP 200, image/png, 512x512 RGBA, **340 non-transparent pixels / 262144** — EXACTLY matches runner's `scenarioR_render_proof.json`. Plume raster (plume-concentration-01KTRNPCV4…) serves at Twin Falls, Idaho bbox.

### Other scenarios (corroboration, not this lens's gate)
- C sandbox: code-exec-request emitted → `sandbox local run: .../infra/python-sandbox/executor.py` → code-exec-result status=ok. WS ordering req idx=65 < res idx=70 (true). result {mean 6.75, max 12}. LOCAL executor, not Vertex code-exec.
- B blocker: real HYDROMT_BUILD_FAILED on /vsigs DEM read; routing correct (flood_hits, gw_hits=0). Genuine env/infra block, agent behaviour correct. B2/B3/B4 legitimately BLOCKED as dependency chain.

## Conclusion
Every NO-SEAM lens assertion holds against artifacts. No inject seams; real Vertex HTTP 200 streamGenerateContent calls; varied real latencies; scenario R provably Gemini-free at the HTTP layer. The runner's PARTIAL verdict and per-scenario calls are consistent with the evidence.
