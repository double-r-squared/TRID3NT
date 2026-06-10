# Kickoff (frozen)

**Job ID:** job-0235-testing-20260609
**Sprint:** sprint-13 (Stage 3, adversarial-verify gated)
**Specialist:** testing
**Status:** assigned

## Task Assignment

Job job-0235-testing-20260609 — Case 2 full E2E acceptance: news -> MODFLOW -> plume (sprint-13 Stage 3, adversarial-verify gated).

### Common rules (GRACE-2 sprint-13 Stage 3 LIVE GATE)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, agents/testing.md, reports/sprints/sprint-13-manifest.md (your job scope).
FIRST ACTION: mkdir -p reports/inflight/<job-id>/evidence ; write audit.md (kickoff verbatim, "# Kickoff (frozen)"); STATE "RUNNING".

### Environment facts (CORRECTED 2026-06-09 — earlier kickoffs said gcloud absent; that was WRONG)
- gcloud IS installed at /home/nate/tools/google-cloud-sdk/bin (add to PATH: export PATH=/home/nate/tools/google-cloud-sdk/bin:$PATH). Authed natealmanza3@gmail.com, project grace-2-hazard-prod, ADC live. Cloud Workflows grace-2-sfincs-orchestrator EXISTS in us-central1.
- SANDBOX DNS: sandboxed Bash cannot resolve googleapis.com — pass dangerouslyDisableSandbox:true on Bash calls that hit GCP APIs or external services. The agent SERVER process is unsandboxed; UI-driven dispatch is unaffected.
- docker daemon NOT reachable (unchanged). Container builds via gcloud builds submit if ever needed — NOT in this job's scope.
- Web dev server: port 5173 (Vite, running). Agent WS: port 8765.

### LIVE-DRIVE RULES (hard, per project memory)
- FORBIDDEN: any __grace2Inject* dev seam. You must drive the REAL agent through the REAL chat input.
- Gemini pacing: shared Vertex quota. Use the FEWEST turns that satisfy acceptance. After session completes, sleep 300s BEFORE returning if another acceptance job follows. On ANY 429 RESOURCE_EXHAUSTED: STOP immediately, do not retry, record verdict=BLOCKED with partial evidence. Never hammer.
- Long-running solves: SFINCS/MODFLOW progress envelopes stream; wait up to 20 min for solver completion before declaring failure (poll the UI, screenshot progress states as evidence).
- NEVER git push. Commit report/evidence at job end. index.lock: wait 5s retry 5x.
- Playwright: use the repo's existing playwright setup (web/ has it). Headless OK. Full-page screenshots at every assertion point.
- Report honestly. A real failure observed live is a FINDING.
Return StructuredOutput.

## Scenario (target: <=5 Gemini turns)
Playwright against http://localhost:5173 — REAL chat, REAL Gemini:
1. Create a new Case. Paste the Case-2 synthetic article text (services/agent/tests/fixtures/case2_news_article.txt) into chat with a one-line ask: "Model the groundwater contamination from this spill: <article>".
2. EXPECT: agent extracts parameters -> parameter-confirmation gate fires in the UI (screenshot it — derived params + demo-aquifer caveat visible).
3. Approve. EXPECT: MODFLOW runs (local mf6 mode), plume layer renders on the map, chat narrates plume extent + max concentration.
4. Acceptance asserts: confirmation envelope appeared BEFORE any MODFLOW dispatch; extracted lat/lon plausibly in the article's state (Idaho); plume layer visible on map (screenshot with layer panel open); narration includes non-zero concentration + area; geography is NOT Florida.
5. Screenshot evidence at each step + save the WS frame log to evidence/.

## Notes
- Composer is model_groundwater_contamination_scenario (job-0228, panel 4/4). If Gemini routes differently (calls atomic tools instead of composer), record the actual chain — a working alternate chain that meets the asserts is still a PASS with routing noted; a confirmation-gate BYPASS is an automatic FAIL (critical).
- This is the FIRST live session after restart: opportunistically screenshot the Thinking indicator + tool cards interleave (no extra turns).
