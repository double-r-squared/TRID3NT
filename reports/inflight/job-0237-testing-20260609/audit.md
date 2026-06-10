# Audit: Conversational analysis layer acceptance + P5 Pelicun bundle

**Job ID:** job-0237-testing-20260609
**Sprint:** sprint-13 (Stage 3 — Live Gate)
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment (frozen)

# Kickoff (frozen)

You are the testing specialist. Job job-0237-testing-20260609 — conversational analysis acceptance + P5 Pelicun bundle (sprint-13 Stage 3, adversarial-verify gated).

## Common rules (GRACE-2 sprint-13 Stage 3 LIVE GATE)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, agents/testing.md, reports/sprints/sprint-13-manifest.md (your job scope).
FIRST ACTION: mkdir -p reports/inflight/<job-id>/evidence ; write audit.md (kickoff verbatim, "# Kickoff (frozen)"); STATE "RUNNING".

### Environment facts (CORRECTED 2026-06-09 — earlier kickoffs said gcloud absent; that was WRONG)
- gcloud IS installed at /home/nate/tools/google-cloud-sdk/bin (export PATH=/home/nate/tools/google-cloud-sdk/bin:$PATH). Authed natealmanza3@gmail.com, project grace-2-hazard-prod, ADC live. Cloud Workflows grace-2-sfincs-orchestrator EXISTS in us-central1.
- SANDBOX DNS: sandboxed Bash cannot resolve googleapis.com — pass dangerouslyDisableSandbox:true on Bash calls that hit GCP APIs or external services. The agent SERVER process is unsandboxed; UI-driven dispatch is unaffected.
- docker daemon NOT reachable (unchanged).
- Web dev server: port 5173 (Vite, running). Agent WS: port 8765.

### LIVE-DRIVE RULES (hard)
- FORBIDDEN: any __grace2Inject* dev seam. Drive the REAL agent through the REAL chat input.
- Gemini pacing: shared Vertex quota. Fewest turns that satisfy acceptance. After session completes, sleep 300s BEFORE returning if another acceptance job follows. On ANY 429 RESOURCE_EXHAUSTED: STOP immediately, verdict=BLOCKED with partial evidence.
- Long-running solves: wait up to 20 min for solver completion. Poll UI, screenshot progress states.
- NEVER git push. Commit report/evidence at job end.
- Playwright: use repo's existing setup (web/). Headless OK. Full-page screenshots at every assertion point.
- Report honestly. A real failure observed live is a FINDING.

## Scenario (target: <=6 Gemini turns) — BUNDLES Wave 4.11 P5 (Pelicun live acceptance)
1. Open existing Fort Myers Case (or create + ask "Model flood damage for Fort Myers using the existing flood layer"). EXPECT: Pelicun chain (compute_impact_envelope) runs -> ImpactPanel slides out with headline numbers -> screenshot. [P5 evidence]
2. Ask "How many structures are impacted above damage state 2?" EXPECT: count_features_above_threshold -> narrated count consistent with ImpactPanel.
3. Ask "Show me the damage distribution as a chart." EXPECT: generate_damage_distribution -> chart-emission -> inline ChartStack card in chat (screenshot) -> click -> gallery opens (screenshot).
4. RELOAD the Case (browser refresh + reselect). EXPECT: charts replay from session document (M4+0230 persistence, LIVE). Screenshot replayed chart.
5. Asserts: counts > 0 and mutually consistent (panel vs narration within rounding); vega-lite chart structurally valid (WS frame); chart persists across rehydration; ImpactPanel renders via production impact-envelope WS path (NOT dev seam — no __grace2Inject in console).

Return StructuredOutput.
