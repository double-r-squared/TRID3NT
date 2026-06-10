# job-0253-testing-20260610 — Stage 3 ROUND 8 (P5 Pelicun chain) — KICKOFF (frozen)

Owner: testing specialist
Opened by: orchestrator

## Premise
Round 8 re-verifies the P5 Pelicun chain after commit **d534f4c** (GCS-URI discipline fix):
- SYSTEM_PROMPT clause: gs:// URIs must be copied VERBATIM from a prior function_response of
  THIS conversation; NEVER constructed / pattern-matched (no cache-style `gs://...-cache/cache/...`).
- `run_pelicun_damage_assessment.hazard_raster_uri` declaration now states the copy-verbatim
  contract and names the exact returned shape (`run_model_flood_scenario`'s `uri` ->
  `gs://...-runs/<run_id>/flood_depth_peak.tif`).

The rounds 6/7 blocker OQ-0252-PELICUN-URI-NOT-WIRED (Gemini fed Pelicun a hallucinated cache
path -> 404 -> no ImpactPanel) should be GONE.

## Environment (verified at kickoff)
- Agent: PID 3164774, `.venv/bin/python -m grace2_agent.main`, started 08:55 (matches fix commit
  d534f4c at 08:55). 89 tools. :8765. DO NOT RESTART.
- Web: vite :5173 HTTP 200.
- A REAL flood layer exists from round 7:
  `gs://grace-2-hazard-prod-runs/01KTS2HNC393Q9PZ563ASB96AA/flood_depth_peak.tif`
  published as `flood-depth-peak-01KTS2HNC393Q9PZ563ASB96AA`. Chain may reuse or re-solve.

## THE scenario (FRESH Case) — single live browser session
1. "Run a flood damage assessment for Fort Myers with Pelicun: model the flood first, then use
   the returned flood depth layer with the NSI building inventory."
   - THE FIX PROOF (uri_discipline): Pelicun's `hazard_raster_uri` argument == the EXACT
     `gs://...-runs/<run_id>/flood_depth_peak.tif` URI returned by run_model_flood_scenario
     EARLIER IN THE SAME CONVERSATION. No invented cache paths. Assert in WS frames / agent log.
   - Pelicun completes -> ImpactPanel slides out with headline numbers. [P5 EVIDENCE]
2. "How many structures are impacted above damage state 2?" -> count consistent with panel.
3. "Show me the damage distribution as a chart." -> chart-emission -> ChartStack -> gallery.
4. Browser refresh + reselect Case -> chart replay.

## LIVE-DRIVE RULES
- NO inject seams (read-only __grace2GetMap OK). <=6 Gemini turns. 429 = STOP.
- NEVER push. Commit report dir only. GCP Bash: dangerouslyDisableSandbox:true.
- Harness adapted from web/tools/stage3_p5_round7_job0252.mjs (fixed settle gate, 25-min budget).

## Verdict rule
per_scenario: uri_discipline / p5_impact / analysis_count / chart_emission / chart_replay.
Overall PASS = uri_discipline PASS + p5_impact PASS + >=2 of {analysis_count, chart_emission,
chart_replay}.
