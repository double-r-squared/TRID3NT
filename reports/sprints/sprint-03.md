# Sprint 03: Foundation (SRS v0.3 M1)

**Status:** active
**Opened:** 2026-06-05
**Closed:** —
**SRS milestones covered:** M1 (Foundation), plus repo realignment from the v0.2→v0.3 pivot

## Goal

At the end of this sprint: the repo is a git repository (MIT license, v0.3 layout, v0.2 artifacts deleted); the SRS Appendix A–D contracts exist as an installable pydantic-v2 package with round-trip tests; a fresh GCP project (Terraform-captured) and a MongoDB Atlas M0 cluster exist with the MongoDB MCP server connection verified; an ADK agent on Gemini 3 streams real replies over the Appendix-A WebSocket core locally; and a browser shows a CONUS MapLibre map whose chat box round-trips with that agent — all verified by `make test` and an acceptance record.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|
| job-0012-infra-20260605 | infra | Repo realignment, git init + MIT license, v0.3 layout | — | approved |
| job-0013-schema-20260605 | schema | Contracts v0 from Appendices A–D (pydantic v2) | — | approved |
| job-0014-infra-20260605 | infra | Toolchain + GCP project + Atlas M0 (Terraform) | 0012; user auth checkpoints | created |
| job-0015-agent-20260605 | agent | ADK hello-world Gemini + Appendix-A WS core + MCP | 0013, 0014 | created |
| job-0016-web-20260605 | web | Web stub: CONUS MapLibre map + chat round-trip | 0013, 0015 | created |
| job-0017-testing-20260605 | testing | M1 acceptance: protocol/contract tests + record | 0015, 0016 | created |

## Execution order

```
stage A (parallel):  job-0012 (repo)        job-0013 (contracts)
stage B:             job-0014 (GCP+Atlas — BLOCKS at user auth checkpoints: `! gcloud auth login`, `! atlas auth login`)
stage C:             job-0015 (agent)
stage D:             job-0016 (web stub)
stage E:             job-0017 (acceptance)
```

Each gate is an in-workflow adversarial review per AGENTS.md. One revision round per job; second failure blocks the job and its dependents. job-0014's user-auth blocks are expected, not failures — the orchestrator resumes after the user authenticates.

## Exit criteria

1. v0.2 artifacts gone; v0.3 layout in place; `git log` shows the initial commit; MIT `LICENSE` at root (job-0012)
2. `packages/contracts` installs in a fresh venv; round-trip tests pass for every Appendix A message type + envelope + claims; the `research_mode` Appendix-A amendment diff and OQ-7 are in the report (job-0013)
3. GCP project exists with the five APIs enabled and `terraform plan` clean; Atlas M0 reachable; MongoDB MCP server round-trip transcript (job-0014)
4. `make run-agent` streams a real Gemini 3 reply over Appendix-A frames locally; `cancel` mid-stream yields cancelled `pipeline-state`; MCP call from the agent verified (job-0015)
5. Browser: CONUS OSM map + chat box streams a live reply; agent-death → disconnected indicator, reconnect works — screenshot + transcript (job-0016)
6. `make test` green: protocol conformance, negative controls, contract suite; acceptance table completed (job-0017)

## Retrospective

_Filled at close._
