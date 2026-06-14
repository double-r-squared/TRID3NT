# job-0242 — Adversarial verify (EVIDENCE INTEGRITY lens)

Verdict: **CONFIRM** the runner's PARTIAL. Every load-bearing claim re-derived from artifacts holds; no contradiction found.

## Critical ordering check (the headline assert)
Re-parsed `evidence/ws_frames.json` independently. Chronological frames:
- 8791ms SENT user-message; 8793ms gemini_generate; 96–111s list_categories + list_tools_in_category (discovery only)
- **164641ms tool-payload-warning, tool_name=run_modflow_job** (warning_id=01KTRGCNWKG8BY7MZK34Q5S5QW)
- 166493ms SENT tool-payload-confirmation, matching warning_id, decision=proceed
Scan for any run_modflow/run_solver/wait_for_completion/publish_layer/map-command frame BEFORE 164641ms → NONE.
**gate_before_dispatch = true — VERIFIED.**

## case2_gate PASS — confirmed
A03_confirmation_gate.png opened directly: inline card "Large response expected", "run_modflow_job", recommendation string with demo-aquifer caveat (K=0.0001 m/s, porosity=0.3, "NOT site-specific hydrogeology", trichloroethylene, Twin Falls Idaho), Proceed anyway / Cancel. Agent log:168 corroborates gate emission. tool_args carry geocoded spill latlon 42.556,-114.470, release_rate~3.07, total_mass~66320.

Naming nuance (NOT a contradiction): gate-emission log line + Gemini function-call are `run_model_groundwater_contamination_scenario` (the composer); the WS warning surfaces the inner gated solver `run_modflow_job`. Consistent.

## case2_plume FAIL — confirmed
Web sent matching warning_id; server rejected (agent log:169 "tool-payload-confirmation for unknown/closed warning_id=...01KTRGCNWKG8BY7MZK34Q5S5QW"). modflow_dispatch_rel_ms=null; plume.materialized=false. Maps A03/A05/99_fatal all over upper-midwest (Saint Paul/Iowa/Nebraska/Lincoln), layer_ids=[qgis-basemap, osm-fallback-basemap] only, in_idaho_bbox=false.

### Root cause independently verified in source
- server.py:2976-2977 `async def handler(websocket)` → `state: SessionState | None = None` — per-CONNECTION local.
- server.py:427 `pending_payload_warnings: dict = field(default_factory=dict)` — per-instance dict.
- server.py:2017 registers future on per-connection `state`; server.py:3175-3177 lookup `state.pending_payload_warnings.get(...)` → None → "unknown/closed".
- Agent log: 4 `connection open` events 03:11:19.705–03:11:20.172 for session 01KTRG7P...DNCN.
- ws.ts: sendEnvelope rides `this.socket` (855-857), reassigned on every `new WebSocket`/reconnect (631,650). Confirmation lands on a later connection whose dict is empty.

## Blast radius — confirmed
server.py:1890 `state.pending_payload_warnings[code_exec_id] = fut` — code-exec/sandbox gate shares the identical per-connection seam. sandbox_gate "Proceed would drop identically" claim holds.

## Downstream BLOCKED — genuinely unreached, not skipped
findings fatal_error = locator timeout on `grace2-cases-new` at stage3_reverify_job0242.mjs:254 (Scenario B nav); rate_limited=false; scenarios.B={}. p5_impact / analysis_count / chart_emission / chart_replay all downstream of the stuck gate → BLOCKED is honest.

## sandbox egress leg PASS — confirmed
sandbox_egress_blocked.json: status=blocked, allowlist guard message (googleapis.com, mongodb.net, localhost...). Gemini-free, valid.

## Conclusion
PARTIAL is correct and honestly reported. The fix proof holds at EMISSION (gate fires inline strictly before any solver dispatch); the E2E completion genuinely fails on a real, source-confirmed per-connection state-split bug. No fabrication, no inject-seam shortcuts, no ordering inversion.
