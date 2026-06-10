# job-0247 report — Stage 3 re-verify ROUND 4 (testing specialist)

**Executed:** 2026-06-10, single LIVE browser session, after the context-carryover fix (commit 74fc0d6).
**Agent under test:** PID 3075277 on :8765, started 05:33:00 — 24s AFTER the fix commit (05:32:36). 89 tools, gemini-2.5-pro, NOT restarted. Env on the live process: GOOGLE_APPLICATION_CREDENTIALS set, GRACE2_SANDBOX_LOCAL=1, GRACE2_MODFLOW_LOCAL=1, GOOGLE_GENAI_USE_VERTEXAI=True, GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod.
**Fix unit-pinned:** tests/test_case_context_reset.py — 3/3 pass (re-run this session).
**Harness:** web/tools/stage3_reverify_round4_job0247.mjs — LIVE, NO inject seams (read-only __grace2GetMap only). B -> C in one session. Gemini turns used: 2 (B1 Fort Myers; C numpy). No 429s. rate_limited=false, fatal_error=null.

**Overall verdict: PARTIAL.**

The headline round-3 blocker OQ-0245-CONTEXT-CARRYOVER-MISROUTE is FIXED and live-verified — the single most important goal of this round. But the overall PASS gate (context_isolation + p5_impact + sandbox_gate_live all PASS) is NOT met: p5_impact BLOCKED (agent asked a clarifying question; no Pelicun chain), sandbox_gate_live FAIL (NEW root cause: code_exec_request not reachable from the default hot-set + no agent self-recovery).

## Per-scenario verdicts

| sub-scenario | verdict | one-line evidence |
|---|---|---|
| context_isolation | PASS | round-3 regression gone: B1 gw_hits=0, C gw_hits=0; both fresh cases open chat=0; C routed straight to code_exec_request (correct numpy code), NOT to the Twin-Falls groundwater composer |
| p5_impact | BLOCKED | B1 produced NO ImpactPanel; agent asked which asset layer to use (NSI vs footprints) rather than dispatching; no Pelicun chain, no impact-envelope frame |
| analysis_count | BLOCKED | depends on B1 impact (never produced); B2 skipped |
| chart_emission | BLOCKED | depends on B1; B3 skipped |
| chart_replay | BLOCKED | depends on B1; B4 skipped |
| sandbox_gate_live | FAIL (NEW: hot-set reachability + no self-widen) | agent called code_exec_request (correct numpy code) -> OutOfAllowedSetError (tool not in hot-set) -> agent narrated a false "I am unable to run Python code directly" instead of widening via list_tools_in_category; NO SandboxCard, NO code-exec-request frame |
| qgis_cache_opportunistic | PASS | service cold-started since round 3; GetCapabilities (HTTP 200) now serves the round-3 plume-concentration-01KTRNPCV4... layer (3 occurrences) — OQ-0245-QGIS-PROJECT-CACHE self-resolves on cold start as USER_UNBLOCK.md predicted |

Overall PASS gate: NOT met (sandbox_gate_live FAIL + p5_impact BLOCKED).

## The headline: context-carryover fix VERIFIED (the round-4 goal)

Round-3 proved every post-switch prompt in a reused WS session re-routed to the prior Case's Twin-Falls groundwater composer. The fix (74fc0d6): case create AND select now clear state.chat_history + turn_count. This round, in the SAME reused anonymous WS session (01KTRRM6RC...), across two fresh cases:

- B1 (Fort Myers flood, fresh case 01KTRRMC6H...): case-open chat=0 layers=0; cache built from the 90811-token system+catalog prefix (NOT prior turns); gemini loop terminal iter=1. ZERO groundwater route (gw_hits=0); agent narrated a flood-domain clarifying question. evidence/B02_route_decision.png.
- C (numpy, fresh case 01KTRSKVZ8...): case-open chat=0 layers=0 (a DIFFERENT new case id); agent's first action was gemini function-call iter=1 tool=code_exec_request args={python_code: np.mean([1,5,9,12]) / np.max([1,5,9,12])} — the CORRECT numpy computation, NOT a geocode/groundwater route. gw_hits=0. evidence/scenarioC_agent_chain.log.

Round-3's hard mis-route (geocode Twin Falls -> solver-confirm gate on every prompt) is GONE in both scenarios. OQ-0245-CONTEXT-CARRYOVER-MISROUTE: CLOSED.

Secondary observation (narration-level, NOT a regression): C's terminal narration confabulated "I can model flood damage for Fort Myers ... I am unable to run Python code directly" — referencing a flood-damage task that was NOT in its (verified-reset) context. The LLM context genuinely reset (chat=0, cache=90811 system-prefix tokens only), so this is model confabulation, not context carryover. The tool routing was correct (code_exec_request). Flagged as OQ-0247-NARRATION-CONFABULATION (low priority).

## Scenario B — flood + P5 (BLOCKED, agent clarification)

Prompt (kickoff verbatim): "Model flood damage for Fort Myers using the existing flood layer." In a fresh case there IS no existing flood layer. The agent (iter=1, terminal, text-only) responded:

  "I can model flood damage for Fort Myers using the existing flood layer, but I need to know which asset layer to use. Should I use the USACE NSI structure inventory, or building footprints?"

Honest, correct clarifying question — NOT a mis-route. But: no run_model_flood_scenario, no run_pelicun_*, no compute_impact_envelope, hence no impact-envelope WS frame and no ImpactPanel (impact_panel_present=false, impact_envelope_frame=false). b1_stalled=false. B2/B3/B4 skipped per the harness gate.

The kickoff allowed either existing-layer Pelicun OR a fresh SFINCS build; the agent chose a third path (clarify-first). Reasonable, but P5 evidence not produced. To exercise P5 in one turn, name the asset layer explicitly OR use a 2-turn flow (run flood -> then Pelicun).

NOTE: the harness B1 settle heuristic burned its full 900s budget because the only pipeline-state frame emitted reads gemini_generate state=running (no terminal pipeline-state is sent when the loop goes terminal at iter=1 with no tool call) — same artifact noted in the round-3 report. Verdict unaffected; authoritative routing proof is the agent log.

## Scenario C — sandbox live gate (FAIL, NEW root cause)

Prompt (kickoff verbatim, self-contained): "Run a quick Python computation: compute the mean and max of the numpy array [1, 5, 9, 12] and print both."

Live agent chain (evidence/scenarioC_agent_chain.log, evidence/scenarioC_root_cause.txt):
- 05:55:31 iter=1 tool=code_exec_request args={python_code: numpy mean/max [1,5,9,12]} — correct routing.
- 05:55:31 ERROR tool dispatch raised ... code_exec_request not in allowed set; consider calling list_tools_in_category(...) first to widen the allowed set. hot-set tools (first 16): [discover_dataset, fetch_dem, fetch_nws_alerts_conus, geocode_location, list_categories, list_tools_in_category, run_model_flood_habitat_scenario, run_model_flood_scenario] — OutOfAllowedSetError (retryable), fed back as a function_response.
- 05:55:40 iter=2 terminal text_chunks=2 — agent did NOT call list_tools_in_category to widen; narrated "I am unable to run Python code directly." and stopped.

Result: NO code-exec-request WS frame, NO SandboxCard, local sandbox never invoked. sandbox_request_present=false, code_exec_request_frame=false. evidence/C01_no_sandbox.png.

Root cause OQ-0247-CODE-EXEC-NOT-IN-HOT-SET (agent, NEW): categories.py:323 categorizes code_exec_request under geographic_primitives, and HOT_SET_TOOLS (categories.py:375) is 8 tools that do NOT include it. The category gate (validate_function_call) rejects a direct code_exec_request call until the agent widens the allowed set via list_tools_in_category("geographic_primitives"). The agent did not perform that two-step recovery — it narrated a false limitation. Distinct from the (now-fixed) context-carryover. The job-0233 sandbox gate machinery (_gate_on_code_exec -> code-exec-request envelope -> SandboxCard -> local exec) was never reached, so it remains unverified live.

Owner: agent. Fix candidates: (a) add code_exec_request to HOT_SET_TOOLS (general-purpose conversational-analysis tool, arguably always-on); (b) auto-widen + retry on OutOfAllowedSetError (hint already in the error); (c) strengthen the system prompt's two-step instruction for code-exec; also consider re-categorizing code_exec_request out of geographic_primitives.

Backend PROVEN Gemini-free (de-risks the fix): evidence/sandbox_local_gemini_free_proof.txt — run_sandbox_local() with the exact numpy array returns status=ok, stdout "mean 6.75 / max 12.0". The local-mode executor works; only the agent's reachability/recovery blocks the live path.

## Gemini-free opportunistic QGIS check — PASS

evidence/qgis_cache_check.txt: GetCapabilities against the live QGIS Server (MAP=/mnt/qgs/grace2-sample.qgs) returned HTTP 200 and now lists plume-concentration-01KTRNPCV4NEN0RRQ3H0QMZQY6 (the round-3 published layer) — 3 occurrences, alongside plume_smoke_job0244 and the flood layers. The service scaled to zero and cold-started since round 3, re-parsing the GCS .qgs, so the round-3 layer now serves. Confirms OQ-0245-QGIS-PROJECT-CACHE self-resolves on cold start exactly as the USER_UNBLOCK.md runbook predicted. NOTE: GetCapabilities-level only (layer in the served project); a full live GetMap render was NOT in scope (Case-2 render user-gated).

## Open questions / gaps surfaced (round-4)

- OQ-0245-CONTEXT-CARRYOVER-MISROUTE: CLOSED. Verified live in B1 + C (gw_hits=0, fresh chat=0 per case). Unit-pinned (3 tests).
- OQ-0247-CODE-EXEC-NOT-IN-HOT-SET (NEW, agent — blocks the live sandbox gate): code_exec_request not in HOT_SET_TOOLS and the agent doesn't self-widen via list_tools_in_category on OutOfAllowedSetError; narrates a false "cannot run Python." Backend proven Gemini-free. Owner: agent.
- OQ-0247-NARRATION-CONFABULATION (NEW, agent — low priority): in a verified-reset fresh case, C's narration invented a "Fort Myers flood damage" reference. Not a tool mis-route (gw_hits=0; correct code_exec_request) and not a context carryover — pure LLM confabulation. System-prompt nudge worthwhile.
- p5_impact (BLOCKED, not a defect): "existing flood layer" prompt in an empty case correctly elicits a clarifying question; P5 needs an explicit asset layer or a 2-turn (model->assess) flow. Re-test: "Run a flood scenario for Fort Myers, then assess damage with Pelicun using the NSI inventory."
- OQ-0245-QGIS-PROJECT-CACHE: self-resolved on cold start (verified). Live env-var fix (USER_UNBLOCK.md) still recommended for sub-10s publish visibility; infra files already carry it.
- Harness B1-settle artifact (carry-over from round 3): terminal-at-iter=1 with no tool call emits no terminal pipeline-state, so the generating heuristic latches and B1 burns its 900s budget. Cosmetic; verdict unaffected.

## Harness notes
- web/tools/stage3_reverify_round4_job0247.mjs; evidence in reports/inflight/job-0247-testing-20260610/evidence/ (findings.json, ws_frames.json, harness_run.log, scenarioB1_agent_chain.log, scenarioC_agent_chain.log, scenarioC_root_cause.txt, sandbox_local_gemini_free_proof.txt, qgis_cache_check.txt, B01/B02/B03/C00/C01 screenshots).
- Turns: 2 (B1 + C). Scenario A (Case-2 render) out of scope (user-gated). No 429s.
