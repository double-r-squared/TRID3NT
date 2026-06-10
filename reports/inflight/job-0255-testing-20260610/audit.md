# job-0255-testing-20260610 — Stage 3 round 10 (P5 FINAL) — FROZEN KICKOFF

## Mission
Re-verify the Pelicun flood-damage chain after the path-mangle repair guard (commit 6804588).

## What changed since rounds 8/9 (job-0253)
- **commit 6804588**: Pelicun `_download_uri_to_local` now REPAIRS LLM-mangled gs:// paths
  (retries the last-two-segment suffix). Proven against the exact round-9 mangled URI
  (real 927KB COG retrieved). Phantom `runs/` prefix no longer breaks download.
- DLML model library restored to venv (today's sync regression); 40/40 Pelicun tests green.
- Agent restarted (:8765, **89 tools** confirmed in /tmp/agent_restart_0254.log). DO NOT restart.
- Base harness: web/tools/stage3_p5_round9_job0253.mjs (terminal-narration gate + 90s quiesce
  + fixed asksClarify heuristic — clarify heuristic must NOT match failure/terminal narrations).

## LIVE-DRIVE RULES
- NO inject seams (read-only __grace2GetMap OK).
- <=6 Gemini turns. 429 = STOP. NEVER push. Commit report dir only.
- GCP Bash: dangerouslyDisableSandbox:true.

## THE scenario (FRESH Case)
1. "Run a flood damage assessment for Fort Myers with Pelicun: model the flood first,
   then use the returned flood depth layer with the NSI building inventory."
   EXPECT: flood chain (~10 min, cache-warm) -> Pelicun call (mangled or verbatim URI —
   guard repairs either) -> DOWNLOAD SUCCEEDS (watch agent log for 'LLM path-mangle guard'
   WARNING — record which path occurred) -> Pelicun completes -> ImpactEnvelope ->
   ImpactPanel slides out with headline numbers [P5 EVIDENCE]. Screenshots: flood map, ImpactPanel.
2. "How many structures are impacted above damage state 2?" -> count consistent with panel.
3. "Show me the damage distribution as a chart." -> chart-emission -> ChartStack -> gallery. Screenshots.
4. Browser refresh + reselect Case -> chart replay. Screenshot.

## Verdict shape
per_scenario: pelicun_download (repaired|verbatim both PASS — record which) / p5_impact /
analysis_count / chart_emission / chart_replay.
Overall PASS = pelicun_download + p5_impact PASS + >=2 of the other 3.

## Owner: testing specialist
