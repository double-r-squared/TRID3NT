# job-0242-testing-20260610 — Stage 3 re-verify bundle (KICKOFF, frozen)

Testing specialist. Job job-0242-testing-20260610 — Stage 3 re-verify bundle: ONE live session covering the Case 2 fix re-verification + job-0237 (analysis+P5) + job-0238 (sandbox) scenarios.

## Working dir
/home/nate/Documents/GRACE-2
FIRST: mkdir -p reports/inflight/job-0242-testing-20260610/evidence; write audit.md (this kickoff verbatim); STATE RUNNING.

## Context — what changed since the failed run (READ FIRST)
- reports/inflight/job-0241-agent-20260610/report.md — the fix wave (commit e712ca6): solver confirm gate NOW EXISTS in the dispatch path (emits a tool-payload-warning card the web client renders inline with Proceed/Cancel); fsspec[gcs] installed so the plume COG uploads to gs:// and publishes.
- The agent was JUST restarted on the fixed code: PID on :8765, 89 tools (catalog http://127.0.0.1:8766/api/tool-catalog). Do NOT restart it again.
- Prior harnesses to reuse/adapt: web/tools/case2_e2e_job0235.mjs (Case 2 driver — note its findings.json schema) — FIX ITS TWO KNOWN BUGS: (1) narration wait must poll for [data-testid=agent-message] terminal state AFTER the composer card completes (it previously stopped at +166s and missed the +237s narration); (2) the long first-turn CachedContent build (~75s) needs the patient-guard it already has — keep it.

## LIVE-DRIVE RULES (hard)
- NO __grace2Inject* seams. Real chat input, real Gemini.
- ONE session budget: target <=12 Gemini turns TOTAL across all three scenarios. Space scenario boundaries by ~120s. On ANY 429: STOP immediately, verdict=BLOCKED for the remaining scenarios, return with partial evidence.
- Screenshot every assertion point. Save WS frame log + agent log excerpts to evidence/.
- NEVER push. Commit reports/inflight/job-0242-testing-20260610 at end.
- GCP-touching Bash (gsutil-style checks) needs dangerouslyDisableSandbox:true.

## Scenario A — Case 2 re-verify (the fix proof; ~4 turns)
1. New Case. Paste the Twin Falls TCE article (services/agent/tests/fixtures/case2_news_article.txt content) + "Model the groundwater contamination from this spill."
2. EXPECT NOW: a tool-payload-warning confirm card renders inline BEFORE any MODFLOW run — showing contaminant=trichloroethylene, location=Twin Falls Idaho, the derived rate, and the demo-aquifer caveat. SCREENSHOT THE CARD. Assert via WS frames: tool-payload-warning frame BEFORE any run_modflow dispatch (gate_before_dispatch MUST be true).
3. Click Proceed. EXPECT: MODFLOW runs (local mf6), plume COG uploads to gs:// (agent log shows "uploaded plume COG to gs://"), publish_layer fires, PLUME RENDERS ON THE MAP over Idaho. Screenshot map + layer panel. Assert layer bbox in Idaho.
4. EXPECT narration bubble (with the FIXED wait): non-zero concentration + area, Idaho named.
5. BONUS assert (no extra turn): also verify the CANCEL path? NO — skip (covered by unit tests; save turns).

## Scenario B — analysis + P5 Pelicun (job-0237 scope; ~5 turns)
1. SAME browser, open/create a Fort Myers Case: "Model flood damage for Fort Myers using the existing flood layer." EXPECT: Pelicun chain -> ImpactPanel slides out with headline numbers. Screenshot. [P5 EVIDENCE — label it]
2. "How many structures are impacted above damage state 2?" EXPECT analytical tool -> narrated count consistent with panel.
3. "Show me the damage distribution as a chart." EXPECT chart-emission -> inline ChartStack card -> click -> gallery opens. Screenshots.
4. Browser refresh + reselect the Case. EXPECT charts replay from the session document. Screenshot.

## Scenario C — sandbox gate (job-0238 scope; ~2 turns)
1. Same Case: "Run a quick Python computation: compute the mean and max of the flood depth raster with numpy and print both."
2. EXPECT: code_exec_request -> SandboxCard REQUEST state with verbatim code + Proceed/Cancel. SCREENSHOT (headline assert: WS ordering request-before-execution). Proceed -> status=ok result card -> narration.
3. Gemini-free egress leg: run sandbox_runner local mode directly on a urllib script; attach status=blocked envelope as evidence.

## Verdict discipline
per_scenario verdicts: case2_gate / case2_plume / p5_impact / analysis_count / chart_emission / chart_replay / sandbox_gate — each PASS/FAIL/BLOCKED + one-line evidence ref. Overall verdict PASS only if case2_gate AND case2_plume PASS (the fix proof); others failing -> PARTIAL with honest detail. Write report.md, STATE READY_FOR_AUDIT, commit.
Return StructuredOutput.
