# Kickoff (frozen)

**Job ID:** stage-3-prep-20260609
**Sprint:** sprint-13 (Stage 3 prep — not a numbered job)
**Specialist:** testing
**Status:** RUNNING

You are the testing specialist doing Stage 3 prep (not a numbered job).

## Common rules (GRACE-2 sprint-13 Stage 3 LIVE GATE)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, agents/testing.md, reports/sprints/sprint-13-manifest.md (your job scope).
FIRST ACTION: mkdir -p reports/inflight/<job-id>/evidence ; write audit.md (kickoff verbatim, "# Kickoff (frozen)"); STATE "RUNNING".

### Environment facts (CORRECTED 2026-06-09 — earlier kickoffs said gcloud absent; that was WRONG)
- gcloud IS installed at /home/nate/tools/google-cloud-sdk/bin (add to PATH in your shell: export PATH=/home/nate/tools/google-cloud-sdk/bin:$PATH). Authed natealmanza3@gmail.com, project grace-2-hazard-prod, ADC live. Cloud Workflows grace-2-sfincs-orchestrator EXISTS in us-central1.
- SANDBOX DNS: sandboxed Bash cannot resolve googleapis.com — pass dangerouslyDisableSandbox:true on Bash calls that hit GCP APIs or external services. The agent SERVER process is unsandboxed; UI-driven dispatch is unaffected.
- docker daemon NOT reachable (unchanged). Container builds via gcloud builds submit if ever needed — NOT in this job's scope.
- Web dev server: port 5173 (Vite, running). Agent WS: port 8765.

### LIVE-DRIVE RULES (hard, per project memory)
- FORBIDDEN: any __grace2Inject* dev seam. You must drive the REAL agent through the REAL chat input.
- Gemini pacing: this is a shared Vertex quota. Use the FEWEST turns that satisfy acceptance. After your session completes, sleep 300s BEFORE returning if another acceptance job follows (pacing gap). On ANY 429 RESOURCE_EXHAUSTED: STOP immediately, do not retry, record verdict=BLOCKED with the partial evidence. Never hammer.
- Long-running solves: SFINCS/MODFLOW progress envelopes stream; wait up to 20 min for solver completion before declaring failure (poll the UI, screenshot progress states as evidence too).
- NEVER git push. Commit your report/evidence at job end: git add reports/inflight/<job-id> && git commit -m "<job-id>: <title>". index.lock: wait 5s retry 5x.
- Playwright: use the repo's existing playwright setup (web/ has it from prior sprints — read reports from job-0178 era for the harness pattern). Headless OK. Full-page screenshots at every assertion point.
- Report honestly. A real failure observed live is a FINDING, not something to talk around — capture it precisely (WS frames, agent logs, screenshot) and verdict accordingly.
Return StructuredOutput.

## Task
1. Restart the agent service so it runs ALL sprint-13 code landed today (the running process on :8765 predates Stage 1/2/M4). Find how it is launched (check Makefile, services/agent/README, main.py, or ps -ef | grep grace2). Kill the old process cleanly (the user may have a browser session open — that is accepted collateral, the restart is mandated by the live-drive memory). Relaunch with: GRACE2_MODFLOW_LOCAL=1 (Case 2 local-mf6 path), GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod, PATH including /home/nate/tools/google-cloud-sdk/bin, and the standard env the old process had (inspect /proc/<pid>/environ of the old process BEFORE killing it to replicate; preserve GOOGLE_* and GRACE2_* and any VERTEX/GEMINI vars EXACTLY).
2. Verify: WS handshake on :8765 (auth-ack), web loads on :5173, the agent registry reports 85 tools (run_modflow_job + code_exec_request + model_groundwater_contamination_scenario + model_nws_flood_event_scenario present — check the tools list via the catalog HTTP endpoint or server logs).
3. Verify the mf6 binary is available for local mode (the path job-0227 evidence used, likely /tmp/mf6 — re-download per services/workers/modflow if missing).
4. Save the relaunch command + env (REDACT any secrets/keys to placeholders) to reports/inflight/stage-3-prep-20260609/restart_runbook.md and a tools-list snapshot as evidence.
Return StructuredOutput (job="stage-3-prep").
