# Audit: ADK skeleton — hello-world Gemini + Appendix-A WS core + MCP verification

**Job ID:** job-0015-agent-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** assigned

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

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
