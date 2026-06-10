# job-0250-testing-20260610 — Stage 3 round 6 (P5-only)

## Frozen kickoff
Re-verify the flood + Pelicun + analysis + charts chain after the HydroMT staging fix
(commits df7b4ba + e715dba). OQ-0248-FLOOD-BUILD-VSIGS reportedly FIXED: HydroMT catalog
inputs now STAGED to local files (gs:// downloaded via storage client).

## Scope
- Agent already running on :8765 (89 tools). DO NOT restart.
- Adapt web/tools/stage3_reverify_round5_job0248.mjs (keep round-5 settle-heuristic fix).
- LIVE drive only: NO inject seams (read-only __grace2GetMap OK). <=7 Gemini turns. 429 = STOP.
- NEVER push. Commit report dir only. GCP Bash: dangerouslyDisableSandbox:true.

## THE scenario (FRESH Case)
1. "Run a flood damage assessment for Fort Myers with Pelicun using the NSI building
   inventory and the existing Fort Myers flood depth layer." Clarification answer:
   "use the NSI inventory". EXPECT chain completes (fresh SFINCS OR Pelicun on existing
   layer). ImpactPanel slides out with headline numbers. [P5 EVIDENCE]. SFINCS leg up to 20 min.
2. "How many structures are impacted above damage state 2?" -> count consistent with panel.
3. "Show me the damage distribution as a chart." -> ChartStack inline -> click -> gallery.
4. Browser refresh + reselect Case -> chart replay.

## Verdict rule
per_scenario: p5_impact / analysis_count / chart_emission / chart_replay.
Overall PASS = p5_impact PASS + >=2 of the other 3 PASS.
