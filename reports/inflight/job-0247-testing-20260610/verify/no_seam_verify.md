# job-0247 — NO-SEAM adversarial verify (REFUTE-by-default)

Lens: harnesses free of `__grace2Inject`; real Gemini latencies; real Vertex calls in the window.
Verdict: **CONFIRM** (runner verdict=PARTIAL upheld under this lens). Severity: none (lens-level — no seam violation, no fabrication).

## 1. Harness seam-free — CONFIRMED
`web/tools/stage3_reverify_round4_job0247.mjs`: zero `__grace2Inject*` *calls*.
The only `__grace2Inject` token is a negative comment (line 26: "NO __grace2Inject* seams").
The only injected-window read is `__grace2GetMap` (line 133) — read-only, permitted.
Driving is all real DOM: `chat-input.fill()+Enter`, `grace2-cases-new` click, `sandbox-card-proceed`, `payload-warning-button-proceed`, `grace2-case-row` click on reload.

## 2. Real Gemini latencies — CONFIRMED
- B1 first-token `elapsed_ms=10035.2`; C first-token `elapsed_ms=10816.8` (≈10s — cold 90811-token session cache build, not 0ms).
- WS-frame deltas corroborate: B1 SENT user-message t_rel=11007 → first chunk t_rel=21122 (~10.1s). C SENT user-message t_rel=1042994 → first chunk t_rel=1053831 (~10.8s).

## 3. Real Vertex calls in window — CONFIRMED
- scenarioB1 log: 2 `aiplatform.googleapis.com` POSTs (cachedContents create + streamGenerateContent), both HTTP 200.
- scenarioC log: 4 (cache + streamGenerateContent ×2 across iter=1 retry-feedback + iter=2), all HTTP 200, model gemini-2.5-pro, location us-central1.
- Verbatim model output captured: iter=1 function-call code_exec_request args `{'python_code': '\nimport numpy as np\nprint(np.mean([1, 5, 9, 12]))\nprint(np.max([1, 5, 9, 12]))\n'}` — genuine model generation, correct numpy.

## 4. Timestamp cross-correlation (no replay/fabrication) — CONFIRMED
Agent-log PDT == WS UTC at exactly +7h, sub-ms:
- B1 user-message: agent 05:38:08,079 PDT == WS SENT 12:38:08.078Z.
- C  user-message: agent 05:55:20,066 PDT == WS SENT 12:55:20.066Z.
Single `starting agent server` at 05:33:01 (24s after fix commit 74fc0d6 @ 05:32:36 PDT); not restarted mid-run.

## 5. Root-cause artifact integrity — CONFIRMED
categories.py:323 categorizes `code_exec_request` under `geographic_primitives`; HOT_SET_TOOLS (categories.py:375) is 8 tools excluding it → the live `OutOfAllowedSetError` is genuine, not synthesized. sandbox_local_gemini_free_proof.txt is a separate direct-executor proof (status=ok, mean 6.75 / max 12.0), correctly NOT conflated with the live (failed) path.

## Nuance (not a refutation)
C's terminal "I am unable to run Python code directly" is a real Gemini iter=2 confabulation (logged, real Vertex call), not a seam artifact and not context carryover (chat=0, cache=90811 system-prefix only). Runner classified it correctly as OQ-0247-NARRATION-CONFABULATION.
The B1 900s settle-budget burn (frames latched at 13) is a real harness heuristic artifact (terminal-at-iter=1 emits no terminal pipeline-state), cosmetic, did not corrupt the routing proof.

No contradiction found between runner claims and artifacts under the NO-SEAM lens.
