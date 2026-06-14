# Wave 4.10 — Stage 0 Routing-Correctness Baseline

**Date**: 2026-06-09
**Owner**: testing specialist (orchestrator-direct landing)
**Workflow**: `wf_974e317e-e8e`
**Agent HEAD**: `e2c8748545baa99949e35d7a874dc957e23a395f`

## Purpose

Capture the BEFORE routing-correctness baseline against which Wave 4.10's architecture jobs (12-category registry, post-hoc validator, Gemini CachedContent, discover_dataset, thought_signature preservation, TOOL_NOT_FOUND typed exception, system-prompt fix) will be measured. AFTER ≥ BEFORE + 10pp on both metrics is the wave acceptance gate.

## Harness

`services/agent/tests/eval_routing_live.py` — anchor-set, Bayesian-adaptive, NO `__grace2Inject*` seams (per `feedback_playwright_must_drive_live_agent`):

- Launches Chromium → navigates `http://127.0.0.1:5173`
- Accepts AuthGate via `grace2_anonymous_accepted` localStorage flag
- Types prompt into real `[data-testid="chat-input"]` → presses Enter
- Snoops every WebSocket frame on agent connection `:8765` for `pipeline-state` envelopes
- Builds observed tool chain → scores against anchor's expected first/full sets
- Aggregates first-tool-correctness, full-sequence-correctness, per-dimension breakdown
- Excludes `gemini_generate` pseudo-step from chain scoring

## Anchor set (5 prompts spanning routing dimensions)

| ID | Prompt | Routing dimension |
|---|---|---|
| A1 | "Where is Big Cypress?" | single-tool-trivial |
| A2 | "Show me protected areas in Big Cypress" | named-existing-endpoint |
| A3 | "Show me HRRR forecast for Florida" | A3-new-endpoint (HRRR not yet registered) |
| A4 | "Model flooding in Naples, Florida" | composite-workflow |
| A5 | "Show me weather alerts clipped to Texas" | geographic-clipping-pattern |

## Baseline results

| Anchor | First-tool | Full-sequence | Observed chain | Duration | Notes |
|---|---|---|---|---|---|
| A1 | PASS | PASS | `[geocode_location]` | 88.3 s | Clean |
| A2 | PASS | FAIL | `[geocode_location]` | 82.7 s | Geocoded then stopped — never dispatched `fetch_wdpa_protected_areas` despite prompt naming the tool. **System-prompt gap.** |
| A3 | FAIL | FAIL | `[]` (refusal) | 34.0 s | Predicted fail — HRRR not registered. Matches expectation. |
| A4 | PASS | FAIL | `[run_model_flood_scenario]` | 130.3 s | 120 s watchdog truncated SFINCS (NFR-P-4 budgets 15 min). **Watchdog tuning needed.** |
| A5 | DRIVER FAIL | DRIVER FAIL | n/a | 0 s | A4's session occupied; A5 chat-input never enabled in 60 s post-A4. **Driver-race remediation needed.** |

**Aggregate (n=5)**:
- **first_tool_correctness_pct = 60.0%**
- **full_sequence_correctness_pct = 20.0%**

**Per dimension**:
- single-tool-trivial: 1/1, 1/1
- named-existing-endpoint: 1/1, 0/1
- A3-new-endpoint: 0/1, 0/1 (expected)
- composite-workflow: 1/1, 0/1
- geographic-clipping-pattern: 0/1, 0/1 (driver fail, NO_DATA)

## Real findings surfaced (will drive Stage 1 scope)

1. **A2: system-prompt does not enforce follow-on dispatch.** When a prompt names a verbatim tool (`fetch_wdpa_protected_areas`) and the agent successfully geocodes a precursor location, the agent currently ends the turn without dispatching the named tool. Need a system-prompt amendment ("when a user names a specific data source, you MUST dispatch it after any precursor steps"). Wave 4.10 Stage 1 gets a new agent-specialist job for this (`B-sys`).

2. **A3: confirms the GRACE-1 endpoint gap thesis.** HRRR is one of 10+ unregistered carry-over endpoints per `project_grace1_endpoint_inventory`. Wave 4.10 Stage 1 A-track lands these.

3. **A4: harness needs per-anchor watchdog override.** SFINCS needs 15 min budget per NFR-P-4. Stage 1 testing job extends harness with `per_anchor_watchdog_seconds` field.

4. **A5: driver race after long-running composite workflows.** When A4's Cloud Workflows execution is still in flight, A5's fresh browser context can't get its WebSocket to `connected` state. Need either explicit cooldown between anchors OR poll on agent's session-state idle signal. Stage 1 testing job adds this.

5. **Two `error` envelopes per anchor consistently**. Recorded in `pipeline_emitter_observations.n_error_envelopes`. Could be session-handshake bookkeeping; could be real backend hygiene. Stage 1 agent-specialist job investigates.

## Pipeline-emitter observations

Per-anchor `n_error_envelopes`: A1=2, A2=2, A3=2, A4=2 (consistent baseline of 2). Independent investigation needed.

## Files

- Harness: `services/agent/tests/eval_routing_live.py` (uncommitted; orchestrator-direct landing)
- Evidence dir: `reports/inflight/wave-4-10-stage-0-baseline-20260609/evidence/`
  - 5 anchor screenshots + 1 driver-fail diagnostic
  - `baseline_metrics.json` (machine-readable)
  - `harness.log`
- Mirrored copies at `/tmp/wave4_10_baseline/`

## Verdict for Stage 1 dispatch

**GO**. Real measurements captured against the real agent with no inject seams. Surfaced 4 actionable findings + confirmed the architecture thesis. The 60% / 20% baseline is the comparison floor.

Stage 1 jobs added based on findings:
- `B-sys` agent system-prompt amendment (named-tool follow-on dispatch) — **adversarial-verify gated**
- `B-rev` TOOL_NOT_FOUND typed exception refactor — **adversarial-verify gated** (already planned)
- Harness refinement: per-anchor watchdog + post-composite cooldown / session-idle poll
- Error-envelope investigation (agent)
