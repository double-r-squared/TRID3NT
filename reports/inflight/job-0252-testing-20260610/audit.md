# job-0252-testing-20260610 — Stage 3 round 7 (P5 FINAL) — KICKOFF (frozen)

Owner: testing specialist
Date: 2026-06-10

## Mandate
Live-driven P5 FINAL verification after the postprocess_flood fix (commit 0b35791,
job-0251-agent). OQ-0250 (gcsfs 0.8.0 NoOpCallback crash) is FIXED — postprocess now downloads
run outputs via google-cloud-storage; fsspec family harmonized to 2026.1.0, storage 3.11.
Proven Gemini-free against the REAL round-6 solve (run 01KTRZSPY7E5A4MPKKX1H00D3Q produced
flood_depth_peak.tif).

This round verifies ONLY the tail (everything upstream proven rounds 1-6):
postprocess -> publish -> flood layer renders -> Pelicun -> ImpactPanel -> analysis -> charts.

## THE scenario (FRESH Case)
1. "Run a flood damage assessment for Fort Myers with Pelicun using the NSI building inventory
   and the existing Fort Myers flood depth layer." (Clarify -> "use the NSI inventory" if asked.)
   EXPECT: chain completes; postprocess succeeds; flood layer publishes + renders; Pelicun runs
   against REAL layer URI; ImpactPanel slides out with headline numbers. [P5 EVIDENCE]
2. "How many structures are impacted above damage state 2?" -> count consistent with panel.
3. "Show me the damage distribution as a chart." -> chart-emission -> ChartStack -> gallery.
4. Browser refresh + reselect Case -> chart replay.

## Rules
- LIVE-DRIVE only. NO inject seams (read-only __grace2GetMap OK). <=7 Gemini turns. 429 = STOP.
- Agent already restarted on the fix (:8765, 89 tools). Do NOT restart.
- QGIS GetMap may 404 (LayerNotDefined) for a NEW layer until periodic cache cold start
  (known user-gated item, job-0245 USER_UNBLOCK.md). If layer publishes but GetMap 404s:
  record it, screenshot layer-panel + GetCapabilities, DO NOT fail the scenario on the overlay
  alone. ImpactPanel/Pelicun legs are the gates.
- Harness: adapt web/tools/stage3_p5_round6_job0250.mjs (keep settle heuristic + 25-min budget).
- NEVER push. Commit report dir. GCP Bash dangerouslyDisableSandbox:true.

## Verdict rule
per_scenario: flood_layer_published / p5_impact / analysis_count / chart_emission / chart_replay.
Overall PASS = flood_layer_published + p5_impact PASS + >=2 of the other 3.
