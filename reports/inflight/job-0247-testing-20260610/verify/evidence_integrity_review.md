# job-0247 ADVERSARIAL VERIFY — lens: EVIDENCE INTEGRITY (REFUTE-by-default)

Verdict: **CONFIRM** (runner verdict=PARTIAL is faithful to raw artifacts). Severity of residual issues: none for evidence integrity.

Re-derived every claim from raw artifacts only (no Gemini). All five integrity checks PASS.

## Check 1 — NO groundwater/Twin-Falls frames in B/C (the context-fix proof) — CONFIRMED
- WS frame stream (ws_frames.json, 24 frames, authoritative client capture): full-text count of `groundwater`=0, `Twin Falls`=0, `Twin-Falls`=0, `solver`=0, `geocode`=0, `contamination`=0, `plume`=0, `run_model_groundwater`=0.
- findings.json: B `groundwater_hit_count:0` `flood_route_hit_count:1`; C `groundwater_hit_count:0`.
- Agent logs: B1 (case 01KTRRMC6H...) terminal at iter=1 text-only, NO tool call. C (case 01KTRSKVZ8...) iter=1 tool=`code_exec_request` with the correct numpy args — NOT a groundwater/geocode route.
- Both fresh cases open chat=0: agent log "case-open ... chat=0 layers=0" (B line 49, C line 73); WS case-open payloads carry `chat_history:[]`. Distinct case ids per scenario.
- Round-3 mis-route (every prompt → Twin-Falls groundwater composer) is absent. CLOSED is justified.

## Check 2 — ImpactPanel numbers match narration — CONFIRMED (vacuously: no panel produced)
- findings.json `impact_panel_present:false`, `impact_envelope_frame:false`. WS frames: `impact_envelope`=0, `pelicun`=0. Harness queried real locator `data-testid="grace2-impact-panel"` (round4 harness line 351).
- B1 narration tail = clarifying question ("which asset layer ... NSI ... or building footprints?") — screenshot B02 matches verbatim. No numbers claimed, none to mis-match. p5_impact BLOCKED is faithful.

## Check 3 — chart replay matches original — CONFIRMED (vacuously: BLOCKED, never reached)
- analysis_count/chart_emission/chart_replay all gated on B1 impact (never produced). findings `b23_skipped:true reason=no_impact_panel`. Harness skipped B2/B3/B4. No chart artifacts exist to mis-represent. BLOCKED is faithful.

## Check 4 — code-exec-request BEFORE sandbox spawn — CONFIRMED (no live sandbox path reached)
- C: agent emitted `code_exec_request` (iter=1, correct numpy) but server raised `OutOfAllowedSetError` (categories.py:642) — tool not in hot-set. findings `sandbox_request_present:false` `code_exec_request_frame:false`. WS frames: `code-exec`=0, `sandbox`=0. No SandboxCard, no spawn — so the ordering invariant is not violated (nothing spawned).
- Root cause independently verified in source: `HOT_SET_TOOLS` (categories.py:375) = exactly the 8 tools cited (run_model_flood_scenario, run_model_flood_habitat_scenario, geocode_location, fetch_dem, fetch_nws_alerts_conus, list_categories, list_tools_in_category, discover_dataset) — `code_exec_request` NOT present. `code_exec_request` categorized under `geographic_primitives` (categories.py:323). Matches scenarioC_root_cause.txt exactly.
- Agent did NOT self-widen via list_tools_in_category at iter=2; narrated false "unable to run Python code directly" (confabulated a Fort-Myers flood reference). Matches findings c_scroll_tail + screenshot C01. sandbox_gate_live FAIL is faithful.

## Check 5 — status=ok result real — CONFIRMED (separate Gemini-free backend probe)
- sandbox_local_gemini_free_proof.txt: run_sandbox_local() on numpy [1,5,9,12] → `status:"ok"`, stdout "mean 6.75\nmax 12.0\n", stderr empty. This is a direct backend invocation, explicitly NOT from the live agent path (correctly scoped as a de-risking probe, not a live-gate pass).

## Additional integrity confirmations
- LIVE-DRIVE compliance: harness (stage3_reverify_round4_job0247.mjs) uses NO inject seams — only read-only `__grace2GetMap` (line 26 comment + line 133); real prompts via chatInput.fill+Enter (line 200-206); fresh cases via UI new-case button (line 226). Compliant with feedback_playwright_must_drive_live_agent.
- Budget discipline: turns_sent=2 (B1 + C), rate_limited=false, page_errors=[]. No 429.
- Fix substrate real: commit 74fc0d6 contains server.py context-reset + tests/test_case_context_reset.py (re-run this session: 3 passed).
- QGIS opportunistic: qgis_cache_check.txt HTTP 200, GetCapabilities lists plume-concentration-01KTRNPCV4... (3 occ). GetCapabilities-level only; report does NOT overclaim a GetMap render. PASS faithful.

## Honest-reporting note (in the runner's favor)
The report does NOT inflate: it labels p5_impact/analysis/charts as BLOCKED (not PASS), sandbox_gate_live as FAIL with a NEW root cause, and explicitly flags the C confabulation as a model artifact (gw_hits=0, correct tool) rather than a context-carryover regression. The overall PARTIAL verdict (not PASS) is consistent with the gate (context_isolation + p5_impact + sandbox_gate_live all-PASS required; two of three not met).

No artifact contradicts any runner claim. CONFIRM.
