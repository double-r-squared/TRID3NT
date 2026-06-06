# Audit: M1 acceptance — protocol/contract tests + sprint-03 exit-criteria record

**Job ID:** job-0017-testing-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** approved

## Task Assignment

**Specialist:** testing
**Prerequisites:** job-0015 and job-0016 (and transitively 0012–0014). Read all five reports first.
**SRS references:** NFR-R-1/R-2 (basic), NFR-P-1 (measure), Appendix A protocol conformance; AGENTS.md *live E2E validation required*; M1 acceptance.

### Scope

1. **Harness** in `tests/`: pytest wiring (`make test`), agent-service subprocess fixture (real WS transport; Gemini may be stubbed at the adapter seam for determinism — the ONLY permitted mock boundary — with one live-Gemini marker test).
2. **WS protocol conformance tests**: envelope discrimination, `user-message` → chunk stream → `done`, `cancel` mid-stream → cancelled `pipeline-state` , malformed frame → A.6 typed `error` and the server survives (negative control), `session-resume` → `session-state`.
3. **Contract suite integration**: `packages/contracts/tests` collected in `make test`.
4. **MCP smoke**: one round-trip against Atlas Flex (cluster `grace-2-dev`) (or qualified if network-gated in CI context).
5. **Sprint exit-criteria verification**: re-run every criterion in `reports/sprints/sprint-03.md`, per-criterion pass/fail with command output. Your report is the sprint's acceptance record.

### File ownership (exclusive)
`tests/**`, pytest config, Makefile `test` target adjustments.

### Environment

Linux (Debian 13) is both dev and CI substrate (PROJECT_STATE decision 2026-06-05). Use `python3 -m venv` for the pytest harness (no conda). The MCP smoke runs against the existing Atlas Flex cluster `grace-2-dev`; if Atlas is network-gated in any future CI context, that smoke runs `qualified` with the reason logged.

### Cross-cutting principles in force
*Live E2E validation required*, *diagnose before fix* (failures name the layer: web vs agent vs contracts vs Atlas vs GCP env), *surface uncertainty*.

### Acceptance criteria (reviewer re-runs)
- `make test` green — full verbatim output
- Real transport everywhere; mock only at the Gemini adapter seam; the live-Gemini marker test passes when run explicitly
- Negative controls present and passing
- Exit-criteria table complete with evidence per criterion

A test that cannot run in this environment is reported `qualified` with the reason — never silently passed or skipped.

## Assessment

`tests/` ships an installable pytest harness (pyproject + conftest + `_agent_runner.py` shim + protocol/ + integration/) driving the real `grace2-agent` WebSocket subprocess through **23 acceptance tests**; `make test` runs **91 contracts + 23 acceptance = 114 tests green** in ~36 s. The single permitted mock is the Gemini adapter (injected via a subprocess shim before the WS server boots); every other layer is real — websockets transport, `grace2_contracts.ws.Envelope` validation, asyncio cancellation across processes, npx-launched MongoDB MCP sidecar, Vertex AI Gemini-2.5-pro on the `live_gemini` opt-in. Sprint-03's **six exit criteria all evidenced**: EC1–EC3, EC5, EC6 pass; **EC4 qualified-pass** (Gemini-2.5-pro substituted for Gemini-3 on Vertex 2026-06-05 — already surfaced for SRS amendment at job-0015 close). One revision round (initial workflow harness blocked the report-write step; closeout populated report.md + applied the latency-label fix + captured a fresh `live_gemini` transcript, then re-review approved).

## Invariant Check

- **Determinism boundary:** pass — `test_negative_controls.py` asserts bare-float on `RainfallIntensity.total_inches` is rejected (Decision M); `ToolCallCompletePayload.metrics` is the structured channel.
- **Deterministic workflows:** pass — `test_protocol_conformance.py` asserts type-discriminator dispatch on every Appendix-A message; no LLM intent-classification phase exercised at the wire (Decision G).
- **Engine registration, not modification:** n/a — no engine surface yet.
- **Rendering through QGIS Server:** n/a — no QGIS path yet.
- **Tier separation:** n/a — no map data in this harness.
- **Metadata-payload pattern:** preserved (extends) — `test_mcp_smoke.py` exercises the MCP path against Atlas Flex as the LLM-facing read surface (FR-AS-4); MongoDB stays the only discovery path.
- **Claims carry provenance:** pass (delegated) — the integration test runs all 91 contracts tests including `NumericClaim`/`ClaimSet` round-trip + provenance shapes.
- **Cancellation is first-class:** pass — `test_protocol_conformance.py::test_cancel_midstream_emits_cancelled_pipeline_state` asserts cancel-to-cancelled-pipeline within NFR-R-3 30 s budget; cancelled state distinct from failed (Invariant 8 / FR-AS-6 verified live).
- **Confirmation before consequence — and no cost theater:** pass — `test_negative_controls.py::test_no_cost_fields_in_confirmation_request_payload` asserts `ConfirmationRequestPayload` has no cost field.
- **Minimal parameter surface:** pass — harness uses only env vars (`GRACE2_TEST_STUB_GEMINI`, `GRACE2_AGENT_PORT`) + the existing Makefile interface; no shadow knobs.

## Dependency Check

- **Prerequisites satisfied:** yes — job-0012 (layout), job-0013 (contracts + 91 tests), job-0014 (GCP project + Atlas SRV + Secret Manager + ADC), job-0015 (running agent + `make run-agent`), job-0016 (`make run-web` for EC5 cross-cite).
- **Downstream impacts:**
  - **Sprint-03 closes** on this audit; M1 acceptance record landed.
  - **First M2 testing job:** picks up OQ-T-1 (real-Gemini latency p50/p95 follow-up — currently only stubbed transport baseline is in `test_latency.py`), OQ-T-3 (cleaner `adapter.stream_reply` rebind in `services/agent/src/grace2_agent/server.py` so the test shim doesn't have to monkey-patch two surfaces), OQ-T-4 (Playwright fixture for in-job EC5 re-run instead of cross-cite). Routing: testing + agent.
  - **CI plumbing job:** when CI lands (post-M1), the `live_gemini` and `live_atlas` markers gate live-cloud tests by environment; `make test` default stays deterministic and offline-runnable.
  - **Outstanding amendments + decisions** (orchestrator carries to user — see sprint retrospective): Gemini-3 substitution (EC4 qualification), NFR-P-1 budget reality, OQ-1 = Cloud Run + WS, A1–A5 Appendix amendments, NFR-C-1 cost correction, OQ-T-3 agent stream_reply rebind, gitignore identifier-exposure decision (Lever A/B/C surfaced separately).

## Decisions Validated

- **Subprocess shim (`_agent_runner.py`) for Gemini stub injection before WS server boots:** agree — alternative (in-process monkeypatch) was correctly rejected because it would require running the WS server in-process and break the real-transport discipline.
- **Atlas / npx self-qualification via `pytest.skip` when unreachable:** agree — matches `agents/testing.md` cloud-dependent rule; alternative (silent skip) correctly rejected.
- **N=10 warm-sample latency methodology with p50/p95/mean:** agree — directly addresses job-0015 OQ-A-2 single-run-snapshot critique. Stubbed transport (50 ms/token sleep) is the right baseline; real-Gemini p50/p95 belongs in a follow-up under `live_gemini`.
- **Live-cloud tests gated by markers (`live_gemini`, `live_atlas`):** agree — `make test` default stays deterministic and offline; CI policy turns markers on per environment. Future CI job that adds the live-cloud markers must surface `qualified` skips, not suppress them.
- **Latency print label retitled to "transport-only p50/p95 — informational; NFR-P-1 measured separately via -m live_gemini":** agree — correctness fix; the prior label could mislead future readers into reading a stubbed ~53 ms baseline as NFR-P-1 satisfaction.
- **`test_make_targets.py` left as-is (string-presence assertions in the Makefile body):** agree as accepted low-severity — the intent is met today. Tightening to `make -n run-agent` dry-run grep is a low-priority follow-up.

## Open Questions Resolved

- **OQ-T-1 (real-Gemini latency p50/p95 belongs to follow-up):** confirmed — `test_latency.py` is transport-baseline (informational); real-Gemini latency follow-up tied to NFR-P-1 mitigation work after job-0015 OQ-A-2 resolves. Routes to next testing job. Surface to user with the NFR-P-1 amendment pile.
- **OQ-T-2 (MCP self-qualification when Atlas unreachable):** confirmed — `pytest.skip` per `testing.md` cloud-dependent rule. Current dev env has both Atlas + npx; smoke runs. CI runner without Atlas allowlist will see `qualified` skips — CI policy must surface, not suppress.
- **OQ-T-3 (agent `server.py` rebinds adapter symbols at import time; test shim must monkey-patch two surfaces):** non-blocking. One-line agent fix (look up `adapter.stream_reply` at call time instead of binding at import) removes the redundancy. Routes to agent specialist in the next agent job.
- **OQ-T-4 (EC5 evidence cross-cited from job-0016 rather than re-run with Playwright):** accepted — re-running would duplicate evidence without new information; `test_make_targets.py` confirms wiring not regressed. Playwright fixture lands when the M2 reviewer wants headed evidence inside the acceptance job.

## Follow-up Actions

- **EC4 qualified-pass — Gemini-2.5-pro substitution:** already in orchestrator's pile (job-0015 follow-up). Carried to sprint-close report.
  - Routing: orchestrator → user (SRS FR-AS-1 amendment). Priority: medium.
- **OQ-T-1 real-Gemini latency follow-up:** when NFR-P-1 amendment lands, the next testing job adds N=10 warm-sample p50/p95 under `live_gemini`.
  - Routing: testing. Priority: medium.
- **OQ-T-3 agent `server.py` stream_reply rebind cleanup:** one-line change in `services/agent/src/grace2_agent/server.py` to import `adapter` module instead of `stream_reply` symbol, so test shim only needs one monkey-patch surface.
  - Routing: agent. Priority: low.
- **`test_make_targets.py` tighten to `make -n` dry-run grep:** accepted low-severity follow-up; intent met today.
  - Routing: testing. Priority: low.
- **CI plumbing** (post-sprint-03 infra job): wire GitHub Actions to run `make test` on PRs; gate `live_gemini` / `live_atlas` markers per environment; surface `qualified` skips.
  - Routing: infra. Priority: medium. Tied to M3+.
- **Gitignore identifier exposures** (independent of sprint-03 closure): user-decision pile A/B/C surfaced separately; hardened `.gitignore` already committed.
  - Routing: orchestrator → user. Priority: independent.
- **PROJECT_STATE update + sprint-03 closure** (this audit closure → sprint-close commit):
  - Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All 13 reviewer adversarial checks pass on live re-run after closeout (AC1 `make test` cold green, AC2 real WS transport, AC3 mock boundary at adapter only, AC4 `live_gemini` marker registration, AC5 cancel within 30 s, AC6 negative controls, AC7 91 contracts collected, AC8 MCP smoke vs real Atlas, AC9 latency capture, AC10 sprint exit-criteria record in report, AC11 file ownership, AC12 commit hygiene, AC13 invariants asserted).
- All six sprint-03 exit criteria evidenced (5 pass + 1 qualified for EC4 Gemini-3 substitution).
- Invariants #1, #2, #6 (extends), #7 (delegated), #8, #9 pass; #3, #4, #5, #10 correctly n/a.
- One revision round (initial harness blocked report-write — closeout populated report.md + label fix + fresh `live_gemini` transcript). Second review approved with one low-severity transparency note (STATE + `.history/` archival are AGENTS.md-mandated, not ownership violations).
- Sprint-03 (M1) is ready to close on this approval.
- Revisions: 1.
