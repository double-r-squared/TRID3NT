# Audit: chart-generation tools + agent chart-emission loop (sprint-13 Stage 2)

**Job ID:** job-0230-agent-20260609
**Sprint:** sprint-13
**Auditor:** Development Orchestrator
**Status:** assigned

## Kickoff (frozen)

You are the agent specialist. Job job-0230-agent-20260609 — chart-generation tools + agent chart-emission loop (sprint-13 Stage 2).

## Common rules (GRACE-2 sprint-13 Stage 2)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, your specialist file in agents/, reports/sprints/sprint-13-manifest.md (your job scope), reports/PROJECT_STATE.md.
- NO Gemini/Vertex generate_content calls. Hard rule. All live evidence is produced programmatically (direct Python invocation, local mf6 binary, vitest/pytest) — never through the chat loop.
- NEVER git push. Commit locally at job end: git add <only your files> && git commit -m "<job-id>: <title>". index.lock conflicts: wait 5s, retry 5x.
- SHARED REGISTRATION FILES WARNING: other Stage 2 agents are concurrently editing services/agent/src/grace2_agent/tools/__init__.py, catalog.py, categories.py, and adapter.py. Re-read each shared file IMMEDIATELY before editing it; keep edits surgical (single anchor); if an Edit fails on a stale anchor, re-read and retry.
- Environment: no docker daemon, no gcloud on this box; mf6 6.5.0 static binary is downloadable and runs locally (see reports/inflight/job-0220-infra-20260609/evidence/mf6_smoke.log); tofu validate only.
- Python venv: services/agent/.venv. Web: npx vitest in web/.
- Report honestly; PARTIAL with documented blockers beats fake success.
- AT JOB END: write reports/inflight/<job-id>/report.md, STATE "READY_FOR_AUDIT".
Return StructuredOutput.

## Inputs in force
- packages/contracts chart_contracts.py: ChartEmissionPayload + SessionChartRecord (job-0223, panel-cleared 2/2)
- Analytical QA tools (job-0224) for data-access patterns

## Scope
1. services/agent/src/grace2_agent/tools/chart_tools.py (NEW), 4 tools:
   - generate_histogram(layer_uri, property) — raster: cell-value histogram (sample cap ~500k cells); vector: property histogram
   - generate_choropleth_legend(layer_uri) — class-break summary chart for the layer's active style
   - generate_time_series(layer_uri) — for temporal rasters/vectors with a time dim; clean error envelope if no time dim
   - generate_damage_distribution(damage_layer_uri) — Pelicun ds_mean distribution bars (read postprocess_pelicun output conventions)
   Each computes data -> builds a Vega-Lite v5 spec (inline values, cap ~2000 rows) -> validates against ChartEmissionPayload -> returns the payload dict as the tool result.
2. Agent loop wiring in adapter.py (SURGICAL — concurrent edits warning): when a tool result IS a ChartEmissionPayload-shaped dict, emit a chart-emission WS envelope (mirror the impact-envelope emission helper _maybe_emit_impact_envelope in server.py — read it first; put the new helper alongside) AND feed a compact data summary (not the full spec) back to Gemini as function_response for narration.
3. Persistence: append SessionChartRecord to the session document via the Persistence singleton (same pattern as telemetry writer M3); replay path = session rehydration includes charts array.

## Acceptance
- pytest tests/test_chart_tools.py: each tool produces a structurally-valid ChartEmissionPayload on synthetic layers (temp rasters/GeoJSON), row caps enforced, no-time-dim error envelope, emission helper triggers on chart payloads and NOT on ordinary tool results, persistence append called.
- [live] run generate_histogram + generate_damage_distribution against real artifacts if present on this machine (look for prior Pelicun FGB outputs under reports/ or /tmp evidence dirs from Wave 4.11; else synthesize) — save the emitted Vega-Lite specs to reports/inflight/<job-id>/evidence/ for job-0231 to use as fixtures.

## File ownership
tools/chart_tools.py, tests/test_chart_tools.py, adapter.py + server.py (surgical emission helper only), persistence append call + surgical registration lines.
