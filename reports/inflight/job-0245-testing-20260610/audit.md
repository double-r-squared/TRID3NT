# job-0245-testing-20260610 — KICKOFF (verbatim)

You are the testing specialist. Job job-0245-testing-20260610 — Stage 3 re-verify ROUND 3 (closing session) after the job-0244 env fixes.

## Working dir
/home/nate/Documents/GRACE-2
FIRST: mkdir -p reports/inflight/job-0245-testing-20260610/evidence; audit.md (kickoff verbatim); STATE RUNNING.

## What changed since round 2 (job-0244 — read its report.md + open questions first)
- commit fdf9b6d: google-cloud-run installed+declared (publish_layer works — PROVEN Gemini-free: the round-2 plume COG published to QGIS Server as plume_smoke_job0244, live WMS URL); GOOGLE_APPLICATION_CREDENTIALS now set in the agent env (GDAL /vsigs/ reads PROVEN); GRACE2_SANDBOX_LOCAL=1 set (sandbox runs locally like mf6).
- Agent restarted on this env: :8765, 89 tools. Do NOT restart.
- Round-2 already PROVED: gate emission, Proceed acceptance (registry fix), MODFLOW solve, GCS upload, narration numbers. THIS round proves the remaining legs: (A) plume RENDERS on the map; (B) the full analysis+P5 chain (vsigs fix unblocks the flood scenario); (C) the live sandbox gate (local mode).
- Harnesses to reuse: web/tools/stage3_reverify_round2_job0244.mjs (A) + stage3_reverify_round2_BC_job0244.mjs (B/C nav fix included). Adapt as round3 copies.

## LIVE-DRIVE RULES (unchanged, hard)
NO inject seams (read-only __grace2GetMap OK). <=10 Gemini turns TOTAL. ~120s between scenarios. On 429: STOP. NEVER push. Commit report dir at end. GCP Bash needs dangerouslyDisableSandbox:true.

## Scenario A — Case 2 render proof (~3 turns)
New Case -> Twin Falls TCE article + "Model the groundwater contamination from this spill." -> confirm card -> Proceed -> EXPECT NOW: agent log "publish_layer succeeded" AND the plume layer RENDERS on the map over Idaho (map zooms/centers Idaho; layer panel shows the plume). Screenshots: card, map+layers. Asserts: gate-before-dispatch (regression), confirmation accepted, publish succeeded, layer bbox in Idaho.
WATCH for the round-2 honest-narration issue: capture the narration verbatim and record whether it claims map-add — this time publish SHOULD succeed so the claim should be TRUE; flag any mismatch either way.

## Scenario B — analysis + P5 (~5 turns)
Fort Myers Case: "Model flood damage for Fort Myers using the existing flood layer" -> with vsigs fixed the SFINCS/Pelicun chain should complete -> ImpactPanel headline numbers [P5 EVIDENCE]. Then count question; then damage-distribution chart -> gallery; then browser refresh + reselect -> chart replay. Screenshots at each. If the agent loop STALLS again (>240s zero progress after a tool failure — the round-2 OQ-0244-LOOP-STALL): capture agent log + WS frames as stall evidence, mark p5_impact BLOCKED, and CONTINUE to Scenario C in a fresh Case (do not burn turns retrying).

## Scenario C — sandbox live gate (~2 turns)
"Run a quick Python computation: compute the mean and max of the flood depth raster with numpy and print both." -> SandboxCard -> Proceed (registry fix) -> LOCAL sandbox executes (GRACE2_SANDBOX_LOCAL=1) -> status=ok -> narration. WS ordering assert.

## Verdict
per_scenario: case2_render / p5_impact / analysis_count / chart_emission / chart_replay / sandbox_gate_live. Overall PASS = case2_render PASS + at least sandbox_gate_live PASS. report.md; STATE READY_FOR_AUDIT; commit.
Return StructuredOutput.
