# Audit: ADK skeleton — hello-world Gemini + Appendix-A WS core + MCP verification

**Job ID:** job-0015-agent-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** approved

## Task Assignment

**Specialist:** agent
**Prerequisites:** job-0013 (contracts package — build against it verbatim; gaps → consumer-pushback motion) and job-0014 (GCP project + Vertex access + Atlas/MCP). Read both reports first.
**SRS references:** FR-AS-1/2/3/4/5, Appendix A (core messages), NFR-P-1, OQ-1 (Cloud Run WS vs Agent Engine — surface with recommendation before M2; for THIS job, local run suffices), M1.

### Scope

1. **ADK app** in `services/agent/`: `google-adk` (pin version), Gemini 3 via Vertex AI on the job-0014 project. Hello-world: a real Gemini round-trip with streaming. Gemini-specific behavior contained in one adapter layer (your Domain Discipline).
2. **WebSocket server** speaking the Appendix-A core subset via the contracts package: `session-resume`/`session-state`, `user-message` → streamed `agent-message-chunk` (delta semantics per A.4, `done: true` terminal), `cancel` → generation interrupt + `pipeline-state` with cancelled state, `error` with A.6 codes. Local run via `make run-agent` (configurable port).
3. **MongoDB MCP integration**: ADK MCP client connected to the job-0014 MCP server; hello-world proof = one real MCP tool call (e.g. list collections / insert+find a test doc in the agent's own session records — note FR-AS-8: session-record writes need no confirmation).
4. **Test client** `scripts/ws_client.py`: send a message, print streamed frames (the live-evidence harness job-0017 builds on).

This is M1 — no workflows, no engine tools, no confirmation UI flow (the hook scaffolding may exist but nothing triggers it yet). Resist scope creep.

### File ownership (exclusive)
`services/agent/**`, `scripts/ws_client.py`, Makefile `run-agent` target. NOT `packages/contracts/` (pushback instead), `web/`, `infra/`.

### Environment

Linux (Debian 13) is both dev and prod substrate (PROJECT_STATE decision 2026-06-05). Cloud Run runs Linux containers. No macOS branching. Use `python3 -m venv` not conda. Gemini 3 via Vertex AI on the GCP project from job-0014; ADC credentials at `~/.config/gcloud/application_default_credentials.json`.

### Cross-cutting principles in force
*Live E2E validation required*, *diagnose before fix*, *surface uncertainty*, *no legacy support pre-MVP* (no LLMProvider abstraction, no Bedrock/Strands shapes — Gemini-only per FR-AS-1).

### Acceptance criteria (reviewer re-runs)
- `make run-agent` then `python scripts/ws_client.py "What is SFINCS?"` → verbatim transcript: streamed `agent-message-chunk` frames from real Gemini 3 (model id in logs), terminal `done: true`, frames validating against the contracts package
- `cancel` mid-stream: generation stops, cancelled `pipeline-state` arrives — transcript
- MCP round-trip transcript (real call against the Atlas Flex (cluster `grace-2-dev` at `mongodb+srv://grace-2-dev.tszeckl.mongodb.net`))
- First-token latency measured vs NFR-P-1 (informational)
- OQ-1 surfaced with recommendation

## Assessment

`services/agent/` ships an installable `grace2-agent` package with the M1 hello-world skeleton: Gemini-only adapter layer (FR-AS-1 containment), Appendix-A WebSocket server using `grace2_contracts.ws` exclusively for serialization, MongoDB MCP stdio sidecar with SRV fetched from Secret Manager via ADC. All four live ACs replay on adversarial re-run: AC1 streamed Gemini reply (20-24 deltas, terminal `done=True`), AC2 cancel-to-cancelled-pipeline-state in **502 ms** (vs NFR-R-3 budget 30,000 ms — Invariant 8 LLM-side verified live), AC3 real MCP `tools/list` (18 tools) + 2× `tools/call` against the live Atlas Flex cluster, AC4 first-token latency captured. One revision round (cc8b2a7) addressed reviewer findings. Three low-severity findings remain in the approved record (all acceptable). Surface to user: Gemini 3 returns 404 on Vertex AI 2026-06-05 (specialist substituted gemini-2.5-pro with single-constant flip path); NFR-P-1 2s first-token budget is not achievable at this configuration without additional mitigation.

## Invariant Check

- **Determinism boundary:** pass — every wire frame is built via `grace2_contracts.ws.Envelope` and serialized via `model.model_dump_json()`; no LLM output is rendered into user-facing narrative without going through the typed envelope. The metrics path will land when tool calls produce typed results in future jobs; this scaffold preserves the seam.
- **Deterministic workflows:** pass (seeded) — server dispatches on `type` discriminator with no intent-classification phase (Decision G honored). For M1 there are no workflows yet; the dispatch shape is deterministic and ready for workflow registration. Tool-call shape (`tool-call-start/progress/complete/failed`) consumed from contracts.
- **Engine registration, not modification:** n/a — no engines yet; nothing in this job modifies engine surfaces or special-cases hazards in the agent core.
- **Rendering through QGIS Server:** n/a — no QGIS interactions in this scaffold.
- **Tier separation:** n/a — no map data in this scaffold.
- **Metadata-payload pattern:** preserved (seeded) — MCP is the LLM-facing read path per FR-AS-4; worker-write paths land in engine jobs. SRV is reached via Secret Manager (NFR-S-3) not committed to repo or container.
- **Claims carry provenance:** n/a — no HEP code yet.
- **Cancellation is first-class:** **pass (LLM-side verified) / partial-extend (Workflows side deferred).** AC2 cancel-to-cancelled-pipeline-state in 502 ms — well under NFR-R-3 30 s budget. `_stream_gemini_reply` catches `CancelledError` and emits `PipelineStep state=cancelled` (distinct from `failed`). `ExecutionHandle.workflows_execution_id` contract pin already in place from job-0013; Cloud Workflows `terminate` call lands when solver does (v0.2 / M5+).
- **Confirmation before consequence — and no cost theater:** pass (scaffolded) — `CONFIRMATION_TRIGGERS` set is empty for M1 with the FR-AS-8 session-records carveout in comments; MCP started with `MDB_MCP_READ_ONLY=true` so confirmation hooks land before any write tools are exposed. Zero `cost`/`usd`/`cents` strings in the codebase.
- **Minimal parameter surface:** pass — Makefile + env vars only (GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GRACE2_GEMINI_MODEL override). No excess flags.

## Dependency Check

- **Prerequisites satisfied:** yes — job-0013 (`grace2-contracts` v0.1.0 with `ws.py`, `execution.py`, `tool_metadata.py`) + job-0014 (GCP project, ADC, Secret Manager SRV, Vertex AI API).
- **Downstream impacts:**
  - **job-0016 (web stub):** consumes the running agent at `localhost:8765` and the Appendix-A frames; codegens TS from contracts/schemas or hand-mirrors. Routing: web.
  - **job-0017 (acceptance suite):** uses `scripts/ws_client.py` and `scripts/mcp_smoke.py` as the live-evidence harness baseline; adds protocol conformance + negative-control + cancellation tests. Routing: testing.
  - **First engine-tool registration job (post-sprint-03):** wires ADK Runner/Agent with `MCPToolset` for autonomous tool-calling — deliberately deferred from this M1 scaffold (OQ-A-3).
  - **Outstanding amendments + decisions** (orchestrator carries to user): Gemini model substitution (gemini-2.5-pro for now; flip to Gemini 3 when Vertex GA's it); NFR-P-1 budget reality; OQ-1 = Cloud Run + WebSocket.

## Decisions Validated

- **Gemini model = `gemini-2.5-pro` (Vertex AI), NOT `gemini-3-pro*`:** agree (deliberate substitution). Specialist probed: all `gemini-3-pro*` / `gemini-3-flash` / `gemini-3-pro-latest` return 404 on Vertex (us-central1 AND global) 2026-06-05; `gemini-2.5-pro` is the highest-tier available. Flip path is a single-constant change (`adapter.GEMINI_DEFAULT_MODEL`) or env override (`GRACE2_GEMINI_MODEL`). **Surface to user:** SRS FR-AS-1 names "Gemini 3"; agent is on 2.5-pro until Vertex GAs Gemini 3 — amendment proposal candidate (clarify "Gemini 3 when available, latest stable otherwise").
- **Gemini containment in `adapter.py` only (FR-AS-1):** agree — `google.genai` imports only in adapter; `server.py` has identifier-level Gemini strings (`_stream_gemini_reply`, `tool_name="gemini_generate"`, log messages) but no behavior leak. Cosmetic rename queued for the Gemini-3 swap (OQ-A-5 / reviewer finding 2 — accepted).
- **MCP transport = stdio sidecar launched via `npx -y mongodb-mcp-server`:** agree — OQ-2 sidecar choice inherited from job-0014; stdio is the proven MCP transport. `MDB_MCP_CONNECTION_STRING` passed via env (NOT argv — `ps` would surface the password). `MDB_MCP_READ_ONLY=true` until FR-AS-8 confirmation hooks land for write tools.
- **Cancellation = asyncio task cancellation against Gemini producer thread:** agree — measured 502 ms cancel-to-cancelled-pipeline-state. `PipelineStep state=cancelled` distinct from `failed` per contract.
- **Virtualenv fallback for Debian (no python3-venv):** agree — same workaround as job-0013. PROJECT_STATE drift surfaced as OQ-A-5; Makefile bootstrap codifies the fallback so future jobs don't re-hit it.
- **`scripts/ws_client.py` lives under `services/agent/scripts/` (NOT repo-root):** agree — file ownership scope says `services/agent/**`; placing the harness with the service it exercises honors the boundary. Kickoff named repo-root path; deviation surfaced explicitly in report's File Ownership section.
- **ADK MCPToolset / Runner wiring deferred to first engine-tool registration job:** agree — M1 ships the MCP seam (live `tools/list` + `tools/call`), not autonomous function-calling through ADK MCPToolset. Stays inside M1 scope.

## Open Questions Resolved

- **OQ-1 (Cloud Run WS vs Agent Engine for the agent service):** resolved with **recommendation = Cloud Run + WebSocket support** (`--use-http2 --session-affinity --min-instances=1`), NOT Agent Engine. Rationale: WebSocket is required by Appendix A.2 (single connection per session, server-initiated frames); Agent Engine targets request/response. **Needs user confirmation before M2** (infra builds Cloud Run service with these flags then). Surface to user at sprint close.
- **OQ-A-1 Gemini model substitution:** resolved with **gemini-2.5-pro** (tentative until Gemini 3 lands on Vertex). Flip path is single-constant.
- **OQ-A-2 NFR-P-1 latency:** unresolved — surface to user as SRS amendment candidate. Cold first-token 20.4 s, warm 3.1 s on `gemini-2.5-pro` Vertex us-central1. Path to NFR-P-1's 2 s budget: (a) `min-instances=1` removes cold-start; (b) Gemini 3 may be faster; (c) consider Gemini Flash for short narration; (d) speculative pre-warm. Reviewer additionally notes warm latency is non-stationary (6.9-8.2 s in re-run); job-0017 should capture p50/p95 over N consecutive warm calls rather than single-run snapshots.
- **OQ-A-3 ADK MCPToolset / Runner integration:** deferred to first engine-tool job. Documented; not blocking M1.
- **OQ-A-4 Contract pushback — `agent-message-chunk` lacks `role` / `finish_reason`:** tentative no-change for v0.1. Web client may push back in job-0016; orchestrator monitors.
- **OQ-A-5 PROJECT_STATE Debian python3-venv drift:** confirmed — both job-0013 and job-0015 hit this. Codified in Makefile bootstrap hint. PROJECT_STATE Environment Facts already names virtualenv fallback; no additional action.

## Follow-up Actions

- **Surface Gemini-3-on-Vertex availability finding to user.** Either (a) wait for Vertex GA (status check in N weeks); (b) propose SRS amendment clarifying "Gemini 3 when available, latest stable otherwise"; (c) explore Agent Engine where Gemini 3 may land first. Recommendation: (b) — SRS amendment.
  - Routing: orchestrator → user. Priority: medium.
- **Surface NFR-P-1 (2s first-token) reality check to user.** Current 3-8 s warm latency on gemini-2.5-pro Vertex. Mitigations + measurement path documented in OQ-A-2. Amendment candidate: relax NFR-P-1 to e.g. 5 s informational + provide a hard NFR-P-1.5 budget that bounds *end-to-end* including stream completion.
  - Routing: orchestrator → user. Priority: medium.
- **Carry OQ-1 = Cloud Run + WebSocket** into the post-sprint-03 infra job that creates the actual Cloud Run service. Flags: `--use-http2 --session-affinity --min-instances=1`.
  - Routing: orchestrator → next infra kickoff. Priority: medium.
- **First engine-tool registration job (post-sprint-03):** wires ADK Runner + Agent with MCPToolset for autonomous function-calling. M1's MCP seam is the foundation. Rename `_stream_gemini_reply`/`gemini_generate` identifiers to `_stream_llm_reply`/`llm_generate` in the same commit.
  - Routing: agent. Priority: future-sprint.
- **Latency capture in job-0017:** acceptance suite should run N consecutive warm `user-message` round-trips and report p50/p95 first-token latency, not single-run.
  - Routing: testing (already in kickoff scope expansion). Priority: medium.
- **PROJECT_STATE update** (this audit closure): GCP `grace-2-hazard-prod` agent running; `make run-agent` works; `grace2-agent` v0.1.0 installable; Vertex AI `gemini-2.5-pro` is the M1 model; MCP sidecar pattern proven; OQ-1 recommendation = Cloud Run + WS.
  - Routing: orchestrator. Priority: high.
- **Close job-0015 and launch job-0016 (web stub).** Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All five kickoff acceptance criteria pass on adversarial live re-run (AC1 streaming, AC2 cancel-to-cancelled in 502 ms, AC3 MCP round-trip, AC4 first-token latency captured-qualified, AC5 OQ-1 recommendation).
- Invariants #1, #2 (seeded), #6 (preserved), #8 (LLM-side verified, Workflows side deferred), #9 (scaffolded), #10 all pass; #3, #4, #5, #7 correctly n/a (deferred to future jobs).
- One revision round (commits 0742c06 → cc8b2a7) addressed initial reviewer findings (report content, NFR-P-1 escalation, OQ-1 trade-offs); second review approved with three low-severity-only findings (all accepted with rationale).
- Three Open Questions surfaced (OQ-1 resolved with recommendation; Gemini-3 substitution + NFR-P-1 latency surfaced for user decision at sprint close).
- File ownership clean (services/agent/** + Makefile; ws_client.py placement deviation documented and accepted).
- Real cloud substrate exercised live: Vertex AI `gemini-2.5-pro` in `grace-2-hazard-prod`, Atlas Flex `grace-2-dev` SRV via Secret Manager, MCP sidecar via npx.
- Revisions: 1.
