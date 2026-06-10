# Audit: Case 3 acceptance — NWS alert → MRMS → SFINCS Idaho

**Job ID:** job-0236-testing-20260609
**Sprint:** sprint-13
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

*(Kickoff frozen at creation — never modified)*

You are the testing specialist. Job job-0236-testing-20260609 — Case 3 acceptance: NWS alert -> MRMS -> SFINCS Idaho (sprint-13 Stage 3).

### Environment facts (CORRECTED 2026-06-09)
- gcloud IS installed at /home/nate/tools/google-cloud-sdk/bin (add to PATH: export PATH=/home/nate/tools/google-cloud-sdk/bin:$PATH). Authed natealmanza3@gmail.com, project grace-2-hazard-prod, ADC live. Cloud Workflows grace-2-sfincs-orchestrator EXISTS in us-central1.
- SANDBOX DNS: sandboxed Bash cannot resolve googleapis.com — pass dangerouslyDisableSandbox:true on Bash calls that hit GCP APIs or external services. The agent SERVER process is unsandboxed; UI-driven dispatch is unaffected.
- docker daemon NOT reachable. Container builds via gcloud builds submit if needed — NOT in this job's scope.
- Web dev server: port 5173 (Vite, running). Agent WS: port 8765.

### LIVE-DRIVE RULES
- FORBIDDEN: any __grace2Inject* dev seam. Must drive REAL agent through REAL chat input.
- Gemini pacing: fewest turns satisfying acceptance. After session completes, sleep 300s before returning if another acceptance job follows. On ANY 429 RESOURCE_EXHAUSTED: STOP immediately, do not retry, record verdict=BLOCKED.
- Long-running solves: SFINCS progress envelopes stream; wait up to 20 min for solver completion.
- NEVER git push. Commit report/evidence at job end.
- Playwright: use repo's existing playwright setup.
- Report honestly.

### Scenario (target: <=4 Gemini turns)
1. New Case. Send: "Show me active flood warnings in Idaho, then model the flood for the most severe one."
2. EXPECT chain: fetch NWS alerts -> warning polygon renders -> MRMS QPE fetch over the polygon -> SFINCS CLOUD solve (grace-2-sfincs-orchestrator — REAL cloud dispatch, ADC is live) -> flood depth layer renders. 3-layer accumulation: warning polygon + MRMS precip + flood depth.
3. REALITY BRANCH: if NO active flood warning exists in Idaho right now (June), agent degrades gracefully — narrates what IS active and offers alternatives. Re-prompt ONCE for any CONUS state with active flood warning ("...in <state> instead") and complete the chain there. Non-Florida geography assert applies. If NO flood warnings exist anywhere in CONUS (rare), run steps 1-2 (alerts + MRMS) on any flood-ADJACENT warning area, document, mark PARTIAL.
4. Asserts: layer extents in the warning's state; SFINCS execution visible in Cloud Workflows (gcloud workflows executions list — unsandboxed Bash); flood depth layer non-empty.
5. Wait through cloud solve (up to 20 min; screenshot progress envelopes).

## Assessment
(filled at audit)

## Invariant Check
(filled at audit)

## Dependency Check
- Prerequisites satisfied: (filled at audit)
- Downstream impacts: (filled at audit)

## Decisions Validated
(filled at audit)

## Open Questions Resolved
(filled at audit)

## Follow-up Actions
(filled at audit)

## Sign-off
- Ready to move to complete: (filled at audit)
