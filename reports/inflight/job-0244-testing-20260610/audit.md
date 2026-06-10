# job-0244-testing-20260610 — kickoff (verbatim)

Job job-0244-testing-20260610 — Stage 3 re-verify ROUND 2 after the job-0243 confirmation-registry fix.

## Working dir
/home/nate/Documents/GRACE-2
FIRST: mkdir -p reports/inflight/job-0244-testing-20260610/evidence; audit.md (kickoff verbatim); STATE RUNNING.

## What changed since round 1 (job-0242 — READ its report + evidence/ROOT_CAUSE_warning_id_dropped.md first)
- job-0243 (commit 768454a): confirmations now resolve via a SESSION-scoped module registry — the Proceed click works regardless of which WS connection delivers it. The round-1 blocker (Proceed dropped as "unknown/closed warning_id") is fixed and unit-proven (35 gate tests).
- Agent JUST restarted on the fix: :8765, 89 tools. Do NOT restart it.
- REUSE the round-1 harness web/tools/stage3_reverify_job0242.mjs (it has the quiescence fix + patient-guard) — copy/adapt as web/tools/stage3_reverify_round2_job0244.mjs.

## LIVE-DRIVE RULES (hard, unchanged)
- NO __grace2Inject* seams (read-only __grace2GetMap OK). Real chat, real Gemini. <=12 turns TOTAL. ~120s between scenarios. On ANY 429: STOP, BLOCKED for the rest, return with partial evidence. NEVER push. Commit your report dir at end. GCP Bash checks need dangerouslyDisableSandbox:true.

## Scenario A — Case 2 approve path (~4 turns) [THE FIX PROOF]
New Case -> paste Twin Falls TCE article (services/agent/tests/fixtures/case2_news_article.txt) + "Model the groundwater contamination from this spill."
Asserts, in WS-frame order: (1) tool-payload-warning card BEFORE any MODFLOW dispatch (screenshot the card); (2) click Proceed -> server log shows "tool-payload-confirmation accepted" (NOT "unknown/closed"); (3) MODFLOW runs local mf6; (4) agent log shows "uploaded plume COG to gs://" (fsspec fix proof); (5) plume layer renders ON THE MAP over Idaho — screenshot map + layer panel, assert layer bbox in Idaho; (6) narration bubble (use the fixed terminal-settle wait) names Idaho + non-zero concentration/area.

## Scenario B — analysis + P5 (~5 turns)
Fort Myers Case: "Model flood damage for Fort Myers using the existing flood layer" -> ImpactPanel headline numbers [P5 EVIDENCE]. Then "How many structures are impacted above damage state 2?" -> count consistent with panel. Then "Show me the damage distribution as a chart." -> inline ChartStack -> click -> gallery. Then browser refresh + reselect Case -> charts replay. Screenshots at each.

## Scenario C — sandbox live gate (~2 turns)
Same Case: "Run a quick Python computation: compute the mean and max of the flood depth raster with numpy and print both." -> SandboxCard REQUEST with verbatim code -> Proceed (NOW WORKS via the registry) -> status=ok result -> narration. WS ordering assert: code-exec-request BEFORE sandbox spawn.

## Verdict
per_scenario: case2_gate / case2_confirm_accepted / case2_plume / p5_impact / analysis_count / chart_emission / chart_replay / sandbox_gate_live. Overall PASS requires case2_gate+case2_confirm_accepted+case2_plume PASS. Honest verdicts; report.md; STATE READY_FOR_AUDIT; commit.
Return StructuredOutput.
