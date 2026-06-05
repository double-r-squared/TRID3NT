# Audit: M1 acceptance — protocol/contract tests + sprint-03 exit-criteria record

**Job ID:** job-0017-testing-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** testing
**Prerequisites:** job-0015 and job-0016 (and transitively 0012–0014). Read all five reports first.
**SRS references:** NFR-R-1/R-2 (basic), NFR-P-1 (measure), Appendix A protocol conformance; AGENTS.md *live E2E validation required*; M1 acceptance.

### Scope

1. **Harness** in `tests/`: pytest wiring (`make test`), agent-service subprocess fixture (real WS transport; Gemini may be stubbed at the adapter seam for determinism — the ONLY permitted mock boundary — with one live-Gemini marker test).
2. **WS protocol conformance tests**: envelope discrimination, `user-message` → chunk stream → `done`, `cancel` mid-stream → cancelled `pipeline-state` , malformed frame → A.6 typed `error` and the server survives (negative control), `session-resume` → `session-state`.
3. **Contract suite integration**: `packages/contracts/tests` collected in `make test`.
4. **MCP smoke**: one round-trip against Atlas M0 (or qualified if network-gated in CI context).
5. **Sprint exit-criteria verification**: re-run every criterion in `reports/sprints/sprint-03.md`, per-criterion pass/fail with command output. Your report is the sprint's acceptance record.

### File ownership (exclusive)
`tests/**`, pytest config, Makefile `test` target adjustments.

### Cross-cutting principles in force
*Live E2E validation required*, *diagnose before fix* (failures name the layer: web vs agent vs contracts vs Atlas vs GCP env), *surface uncertainty*.

### Acceptance criteria (reviewer re-runs)
- `make test` green — full verbatim output
- Real transport everywhere; mock only at the Gemini adapter seam; the live-Gemini marker test passes when run explicitly
- Negative controls present and passing
- Exit-criteria table complete with evidence per criterion

A test that cannot run in this environment is reported `qualified` with the reason — never silently passed or skipped.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
