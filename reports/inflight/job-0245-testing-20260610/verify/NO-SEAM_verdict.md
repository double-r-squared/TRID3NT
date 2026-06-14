# job-0245 ROUND 3 — Adversarial verify, lens=NO-SEAM

Verdict: **CONFIRM** (runner's PARTIAL stands; harness/latency/Vertex authenticity holds; no seam artifacts).

## What the lens demands
Harnesses free of `__grace2Inject*`; real Gemini latencies; real Vertex calls in the window.

## Evidence

### 1. Harness is seam-free (real UI input only)
- `web/tools/stage3_reverify_round3_job0245.mjs`: the ONLY `__grace2Inject` occurrence is line 17, a comment **forbidding** it ("NO __grace2Inject* seams. Read-only __grace2GetMap permitted.").
- All `__grace2` runtime usages are read-only getters: `__grace2GetMap` (L96, map state read) + `__grace2ActiveCaseId` (L379, case-id read). No setters/pushers/emitters.
- No `WebSocket` / `.send(` / `postMessage` / `dispatchEvent` injection in the harness (clean grep, exit 1).
- Prompts sent via real chat input: `sendPrompt()` (L163-173) does `chatInput.fill(text)` + `chatInput.press("Enter")`. Confirmations via real `proceed.click()` (A L300, C L560).
- No `__grace2Inject*` anywhere in the evidence logs (clean grep over evidence/, exit 1).

### 2. Real Gemini latencies (not mocked)
- Live session `01KTRNN09HZ5BWVQXE5D9C9Y3Q` first-token latencies: A=15768.7ms, B=17666.3ms, C=14081.3ms. These are authentic gemini-2.5-pro first-token times — uninjectable.
- B-gate timeout is a real 300s TTL: gate `01KTRP92...` at 11:56:56Z → CONFIRMATION_TIMEOUT at 12:01:56Z = exactly 300s (ttl_seconds=300). Honest cancellation narration followed.
- Groundwater solve spanned 04:46:40→04:49:48 local (~3min worker cold start), matching WS `run_model_groundwater_contamination_scenario` complete frame at t_rel 230259ms.

### 3. Real Vertex calls in the window
- 14 POSTs to `us-central1-aiplatform.googleapis.com/.../gemini-2.5-pro:streamGenerateContent` HTTP/1.1 200 OK in agent_log_round3.log, timestamped 04:43→05:14 local.
- Real CachedContent created: POST `.../cachedContents` 200 → `name=projects/425352658356/.../cachedContents/6296266303241977856 tokens=90811 ttl_s=3600`.
- Real token accounting with cache hits: every live-session iter logs `cached=90811 ... hit=True`. Uninjectable.

### 4. Per-scenario verdicts are corroborated, not seam-inflated
- **case2_render FAIL**: publish PROVEN Gemini-free via `run_v2` worker dispatch (scenarioA_agent_chain.log L37-40: dispatch grace-2-pyqgis-worker → CONDITION_SUCCEEDED) + GCS proof (qgs_layer_proof.txt: canonical .qgs gen 1781092174312777 updated 11:49:34Z, contains round-3 layer True, 11 maplayers). Render blocked by real QGIS Server LayerNotDefined (getmap_layernotdefined.xml ServiceException). `plume.materialized=false`, map center {-95.5,37} z4 — genuine FAIL, not a seam mask.
- **p5_impact / sandbox_gate_live BLOCKED**: the correct distinct prompts WERE sent via real input (WS SENT:user-message frames: "Model flood damage for Fort Myers..." t_rel 639402; "Run a quick Python computation..." t_rel 1670826). The agent genuinely mis-routed both to the Twin-Falls groundwater composer (real-Gemini context carryover off the 90,811-token cached prefix) — a real behavior surfaced BY live driving, not a harness artifact. No SandboxCard / code_exec_request frame in the live session (findings.json C: both false).

### 5. Honest disclosure of the leftover session
The report (L56) honestly flags a separate round-2-era driver session `01KTRMGNNFHT2GD042DVP6ZDC6` (04:43-04:45) that DID emit a `code_exec_request` (numpy) + Pelicun chain, and explicitly does NOT count it as a PASS. The live-driven round-3 session is correctly isolated to `01KTRNN09...`. This is the opposite of seam-cheating — the runner declined to claim a non-live result.

## Process stability
Single stable agent: registry loaded 04:37:47 (89 tools), all live turns 04:46+ on one PID — not restarted (per kickoff).

## Conclusion
NO-SEAM lens: PASS. Every leg of the runner's PARTIAL verdict is backed by seam-free, real-Vertex, real-latency artifacts. The mis-routing and render-block are authentic live failures honestly reported, not injection masks. No contradiction found.
