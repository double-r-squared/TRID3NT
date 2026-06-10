# Audit: Case 3 workflow composer — NWS alert → MRMS → SFINCS

**Job ID:** job-0229-agent-20260609
**Sprint:** sprint-13 (Stage 2)
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

# Kickoff (frozen)

You are the agent specialist. Job job-0229-agent-20260609 — Case 3 workflow composer: NWS alert -> MRMS -> SFINCS (sprint-13 Stage 2).

## Common rules (GRACE-2 sprint-13 Stage 2)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, your specialist file in agents/, reports/sprints/sprint-13-manifest.md (your job scope), reports/PROJECT_STATE.md.
FIRST ACTION: mkdir -p reports/inflight/<job-id>/ ; write audit.md with this kickoff verbatim under "# Kickoff (frozen)"; STATE file "RUNNING".
- NO Gemini/Vertex generate_content calls. Hard rule. All live evidence is produced programmatically (direct Python invocation, local mf6 binary, vitest/pytest) — never through the chat loop.
- NEVER git push. Commit locally at job end: git add <only your files> && git commit -m "<job-id>: <title>". index.lock conflicts: wait 5s, retry 5x.
- SHARED REGISTRATION FILES WARNING: other Stage 2 agents are concurrently editing services/agent/src/grace2_agent/tools/__init__.py, catalog.py, categories.py, and adapter.py. Re-read each shared file IMMEDIATELY before editing it; keep edits surgical (single anchor); if an Edit fails on a stale anchor, re-read and retry.
- Environment: no docker daemon, no gcloud on this box; mf6 6.5.0 static binary is downloadable and runs locally (see reports/inflight/job-0220-infra-20260609/evidence/mf6_smoke.log); tofu validate only.
- Python venv: services/agent/.venv. Web: npx vitest in web/.
- Report honestly; PARTIAL with documented blockers beats fake success.
- AT JOB END: write reports/inflight/<job-id>/report.md, STATE "READY_FOR_AUDIT".
Return StructuredOutput.

## Inputs in force
- fetch_mrms_qpe (enhanced job-0226; accumulation aliases, dynamic-1h ttl)
- model_flood_scenario v2 forcing_raster_uri branch (job-0225, area-mean netamt)
- Existing fetch_nws_alerts tool (find its exact name in tools/)

## Scope
services/agent/src/grace2_agent/workflows/model_nws_flood_event_scenario.py (NEW): composer chaining:
1. fetch NWS active alerts for a bbox/state; filter to Flood Warning / Flash Flood Warning; select the highest-severity (or caller-specified index); extract the warning polygon
2. fetch_mrms_qpe over the polygon bbox (accumulation="24h" default)
3. model_flood_scenario(forcing_raster_uri=mrms_uri) over the warning area
4. return the 3-layer accumulation contract: {warning_polygon_layer, mrms_precip_layer, flood_depth_layer} so the UI renders all three
Graceful degrade: if no active flood warnings in the queried area, return a structured no-op result (not an exception) listing what WAS active — the agent narrates honestly.
Tool registration: surgical (shared-file warning applies).

## Acceptance
- pytest tests/test_model_nws_flood_event_scenario.py: alert filtering + severity selection, polygon extraction, chain ordering with mocked fetchers + mocked SFINCS, 3-layer return shape, no-warning degrade path.
- [live, best-effort] hit the real NWS alerts API once (no key needed, no Gemini) for any CONUS flood warning right now; if one exists, run steps 1-2 for real (MRMS fetch over its bbox) and save the layer summary as evidence; SFINCS step mocked (solver runs are Stage 3 scope). If none active, evidence = the degrade-path output for Idaho. Do not loop/retry the API.

## File ownership
workflows/model_nws_flood_event_scenario.py, tests/test_model_nws_flood_event_scenario.py + surgical registration lines.

## Assessment
(filled at audit)
