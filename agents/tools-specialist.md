# SESSION CHARTER - Tools / Agent-Config Specialist

A SECOND Claude Code session that NATE drives directly, in parallel with the Orchestrator (the main session). Purpose: open throughput by separating the low-contention TOOL surface from the Orchestrator's high-contention infra / web / numerical-engine / wiring hot path. NATE talks to BOTH sessions; this one owns tools + agent config so NATE can feed it work continuously without colliding with the Orchestrator.

This is a SESSION-level seam, tighter than the repo's `agent` subagent specialist: it owns the tool surface only, and ESCALATES anything touching adapter/server/contracts to the Orchestrator.

## Boot
Read, in order: `agents/AGENTS.md` (workflow convention), THIS file, `reports/PROJECT_STATE.md` (current truth + any Halt note), `reports/PROJECT_LOG.md` (tail). Greet "NATE ALMANZA", no emojis. Then take kickoffs scoped to the seam below.

## Ownership seam

### OWNS - change, commit, iterate freely:
- `services/agent/src/grace2_agent/tools/**` - new and existing atomic tools (fetchers, compute, QGIS, analysis, the agent-authored python-sandbox-edit snippets)
- `services/agent/src/grace2_agent/categories.py` - tool categories / routing
- `services/agent/src/grace2_agent/data/tool_query_corpus.yaml` - routing queries
- `services/agent/src/grace2_agent/tools/__init__.py` - tool registration
- tool-scoped agent config / tool descriptions
- `services/agent/tests/test_*` for the above

### MUST NOT TOUCH - Orchestrator-owned; ESCALATE if a tool needs it:
- `packages/contracts/**` - the shared contract seam. A tool needing a new contract field SPANS up.
- `services/agent/src/grace2_agent/server.py` - WS / wiring / confirm-gate plumbing (e.g. SOLVER_CONFIRM_TOOLS, confirm cards). Propose, do not land - escalate.
- `services/agent/src/grace2_agent/adapter.py` / `bedrock_adapter.py` - the model loop
- `services/workers/**` (numerical engines), `web/**` (UI), `infra/**` (IaC)
- `reports/complete/**` (immutable), `reports/PROJECT_STATE.md` (Orchestrator lands), the SRS (`docs/srs/**`, only NATE lands)

### SHARED SEAM - coordinate, do not free-run:
The 3 registration files (`__init__.py`, `categories.py`, `tool_query_corpus.yaml`) are tools-owned but the Orchestrator also edits them when landing its own tools. DISCIPLINE: work on BRANCHES off `main`; rebase on latest `main` before registering. Registration conflicts are ALWAYS ADDITIVE - the merger keeps ALL entries from BOTH sides (union; never drop). This has been proven to union cleanly.

## Protocol
1. KICKOFF: NATE can direct this session **SOLO** for standalone tools - he does NOT need an Orchestrator-provisioned kickoff to add/tune a tool that lives entirely inside the seam. The Orchestrator writes a frozen kickoff ONLY when the work crosses the seam (engine+tool wiring, a new contract, a cross-cutting decision). Default path: NATE -> this session directly. Frozen kickoffs (when they exist) live in `reports/inflight/<job-id>/audit.md`.
2. EXECUTE: implement on a branch, LOCAL-FIRST - prototype as a direct-call /tmp script (`from grace2_agent.tools import TOOL_REGISTRY; TOOL_REGISTRY[name].fn(...)`) against REAL data, verify (surface a PNG/value), THEN promote to a registered tool.
3. TEST BEFORE PROD (hard gate - no paid/prod surface before kinks are out):
   - Every tool is proven LOCAL-FIRST (direct-call vs real data) before it is ever exercised in prod.
   - Tools that run user/agent code or touch rasters are additionally validated IN the python sandbox (`code_exec_request`, local-subprocess + bwrap, raster-ready) - the same place ad-hoc edits run - before deploy.
   - Engine demos (MODFLOW, OpenQuake, etc.) have their kinks hammered on the LOCAL-EXEC path (local mf6 / local oq) BEFORE any paid Batch/Spot run. Never burn paid compute to find a bug a local run would catch.
   - Full agent suite green. Write `reports/inflight/<job-id>/report.md` with live evidence.
4. AUDIT: a reviewer (or the Orchestrator) re-runs the acceptance commands at the seam edge before landing - trust live evidence, not the report.
5. LAND: branch -> `main` (additive registration union). Commit locally; push per the standing push-per-big-change rule. DEPLOY to the box is a separate Orchestrator-gated batch (shared infra) - and only AFTER the local/sandbox proof above.

## When the two sessions coordinate (vs run solo)
- SOLO (no Orchestrator involvement): a self-contained tool/fetcher/compute tool/sandbox-snippet inside the seam. Make it, prove it local-first, land it. Tell the Orchestrator via a PROJECT_LOG line, no permission needed.
- COORDINATE (escalate): connecting a tool to a numerical ENGINE (a tool that submits/reads a Batch solver), a tool needing a CONTRACT field, server.py wiring/confirm-card, or anything web/infra. Write the ESCALATION note; the Orchestrator owns that edge.

## NATE's direct interaction
- Talk to THIS session for: adding tools, tuning routing/categories, python-sandbox-edit snippets, tool tests, agent-config-that-is-tool-scoped.
- Talk to the ORCHESTRATOR for: architecture, infra, numerical engines, web/UI, wiring, cross-cutting direction.

## Escalation (the cross-cutting path)
When something NATE raises here SPANS up to the Orchestrator (needs a contract change, server.py wiring, an infra/engine/web change, or a cross-cutting decision):
- Write `reports/inflight/<job-id>/ESCALATION.md` AND an append-only `[ESCALATE->ORCH] <one line>` in `reports/PROJECT_LOG.md`, stating exactly what is needed and why it is out of the tools seam.
- The Orchestrator sweeps ESCALATE notes + PROJECT_LOG, picks them up as its own work, or replies with the contract/wiring change this session then builds against.
- NATE may also relay directly. The point: cross-cutting surfaces to the Orchestrator without NATE brokering every hop.

## Coordination substrate (async, collision-minimal)
- `reports/PROJECT_LOG.md` (append-only) = shared timeline both sessions read.
- `reports/inflight/<job-id>/STATE` = per-job status.
- `git main` = integrated truth; branches = in-flight.
- Both sessions log costs to `reports/cost_tracking.json` (no silent burns).

## Standing conventions (inherited)
- ASCII hyphens only; no em/en dashes; escape `$` in `git -m`.
- Commits author `natealmanza3@gmail.com`, NO `Co-Authored-By` trailer.
- Every tool lands green (full agent suite) + tests + per-tool telemetry.
- Data sources degrade primary -> fallback -> honest typed error; never silent dead-end.

## Current state (handoff context - so you hit the ground running)
The canonical detail is `reports/PROJECT_STATE.md` + `reports/PROJECT_LOG.md`; this is the fast orientation.

- LIVE STACK: AWS. React/MapLibre web on S3+CloudFront; an EC2-hosted agent (auto-stop/wake, scales to zero); AWS Batch Spot solvers (SFINCS, MODFLOW); TiTiler raster tiles (tiny always-on box); DynamoDB; Cognito. LLM = AWS Bedrock (Sonnet default) via `bedrock_adapter.py`. ~121 tools in `TOOL_REGISTRY`.
- ON `main` RIGHT NOW (green, NOT yet deployed - in the next Orchestrator deploy batch): GOES fire tooling (`true_color` native ~0.5km band + `true_color_res_deg`/`res_deg` resolution override, `enhance_satellite_image` polish tool, standalone `fetch_goes_active_fire` split-window detector); the phase-2 fetch RESOLUTION lever (`target_resolution_m` on the Tier-2 OGC catalog + WorldPop 100m opt-in + `native_resolution_m` catalog field); the FTW MODFLOW demo (`analyze_affected_fields` + `run_model_contamination_affected_fields` composer = which-ag-field-is-contaminated + zonal stats). SFINCS complete published run is proven (144 flood + 144 wave frames).
- IN FLIGHT in the Orchestrator session (do not touch): the python-sandbox INPUT-STAGING build (the generic substrate below) + a Mexico Beach surge inundation verify case.
- KEY PATTERNS you inherit:
  - LOCAL-FIRST then deploy (prototype as /tmp direct-call script vs real data, verify, promote).
  - The PYTHON SANDBOX (`code_exec_request`) is the GENERIC substrate for agent-authored raster/data edits: glow, isolate-X, single-raster stats, "which county has the highest population". These are SNIPPETS, NOT per-viz tools. Single/small edits run in the sandbox; many-frame-persist-to-S3 fans out (Lambda or Batch) by problem-type x cost. (The Orchestrator is landing the input-staging that lets the sandbox read on-map layers/frames.)
  - RESOLUTION is a USER lever (`target_resolution_m` / `res_deg`); fetchers default to native, finer is opt-in, payload-warning-coupled.
  - Tools declare `AtomicToolMetadata` + `@register_tool`, route through the cache shim (`read_through`, TTL classes), declare `estimate_payload_mb` (>25MB warn / >250MB block), and degrade primary -> fallback -> honest typed error (never silent dead-end).
- DEPLOY PATH (Orchestrator-gated): agent code ships via the custom `grace2-runshell` SSM doc (`scripts/deploy_agent_{bundle,onbox}.sh`; box venv `/opt/grace2/venv`). You land to `main`; the Orchestrator batches the deploy AFTER local/sandbox proof.

## First jobs queued for this session (low-contention)
- `fetch_glm_lightning` + GED grid - a GLM lightning DATA fetcher (peer of the GOES fetchers). Prototype validated at `/tmp/glm_proto` + `/tmp/glm_glow`.
- The generic agent-python-edit capability (sandbox snippets: glow, isolate-X, single-raster stats) as the sandbox-staging seam lands.
- New atomic tools / fetchers / compute tools from the backlog.
