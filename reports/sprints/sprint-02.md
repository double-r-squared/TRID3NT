# Sprint 02: Bootstrap — plugin skeleton + Bedrock echo chat (SRS v0.2 M1)

**Status:** aborted
**Opened:** 2026-06-04
**Closed:** 2026-06-05 (aborted)
**SRS milestones covered:** SRS v0.2 M1 (project bootstrap + plugin skeleton) — superseded by SRS v0.3 mid-execution

## Goal

At the end of this sprint a developer (or agent) on this Mac can: create the blessed `grace2` conda env (QGIS + pinned Strands + boto3) from `environment.yml`; see the selected GeoAgent portions vendored as project source with `THIRD_PARTY_NOTICES` in place; run `make deploy-plugin && make run-qgis` to get QGIS with a dockable chat panel; run `make run-agent` for a local agent service; and have a typed chat message stream back token-by-token from real Bedrock (Ollama as local fallback), over a versioned WebSocket contract — with `make test` verifying the round-trip, plugin load, and contracts headlessly.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|
| job-0006-infra-20260604 | infra | Dev env (conda QGIS + agent deps) + repo scaffold | — | created |
| job-0007-agent-20260604 | agent | Vendor GeoAgent snapshot + THIRD_PARTY_NOTICES | 0006 | created |
| job-0008-schema-20260604 | schema | Contracts v0: WS protocol, pipeline state, intent, envelope stubs | — | created |
| job-0009-plugin-20260604 | plugin | QGIS plugin skeleton: chat dock + WS client (vs stub server) | 0006, 0008 | created |
| job-0010-agent-20260604 | agent | Local agent service: WS server + Bedrock/Ollama streaming | 0007, 0008 | created |
| job-0011-testing-20260604 | testing | Smoke harness + sprint acceptance verification | 0009, 0010 | created |

## Execution order

```
stage A (parallel):  job-0006 (long pole: conda install)    job-0008 (venv-fallback verification)
stage B (parallel):  job-0007 (gated on 0006)               job-0009 (gated on 0006 + 0008)
stage C:             job-0010 (gated on 0007 + 0008; same specialist as 0007 → naturally serialized)
stage D:             job-0011 (gated on 0009 + 0010)
```

Each gate is an in-workflow adversarial review per AGENTS.md § "Execution Model — Workflows". One revision round per job; second failure blocks the job and its dependents.

## Exit criteria

1. `conda run -n grace2 python -c "from qgis.core import Qgis; print(Qgis.QGIS_VERSION)"` prints ≥ 3.34, and the pinned `strands-agents` + `boto3` import cleanly (evidence: job-0006 report)
2. Vendored GeoAgent portions import as `grace2_agent.*`; excluded portions (leafmap/anymap, GEE, MapLibre) absent by grep; `THIRD_PARTY_NOTICES` carries MIT text + pinned commit SHA (evidence: job-0007 report)
3. Contracts importable from `grace2_contracts` with generated JSON Schemas in `docs/contracts/`, including `load_layer` and `pipeline_state` shapes (evidence: job-0008 report)
4. Plugin loads in conda QGIS, dock opens, streams a stubbed conversation, survives agent death with visible status — screenshots + transcript (evidence: job-0009 report)
5. Live chat round-trip through the real agent service: streamed Bedrock response (model ID in transcript) AND Ollama by config switch; `cancel` mid-stream lands a cancelled `pipeline_state` (evidence: job-0010 report)
6. `make test` green in the `grace2` env: WS round-trip with negative controls, plugin-load smoke, contract suite; sprint acceptance table completed (evidence: job-0011 report)

## Retrospective

Aborted on 2026-06-05: SRS v0.3 pivoted the product to a web-based AI workbench (React/MapLibre + Google ADK/Gemini + QGIS Server + MongoDB Atlas), invalidating the QGIS-plugin architecture mid-Stage-A. Execution had been user-halted the prior evening with 0006/0008 near-done and unreported; no review or audit ever ran.

**Salvage (real value retained):**
- `grace2` conda env (QGIS 3.40.3-Bratislava, verified) — directly reusable for local PyQGIS worker development in v0.3 (FR-QS-6); strands/boto3 are dead weight to strip when `environment.yml` is reworked.
- pydantic v2 contract decision — now SRS-anchored (Appendix D models are pydantic).
- The contracts-first job sequencing and the parallel-then-gate workflow execution pattern, both validated in practice.

**Write-off:** v0.2-shaped contract modules (`src/grace2_contracts/`), plugin scaffold (`plugin/grace2_plugin/`), agent skeleton (`src/grace2_agent/`), Makefile/pyproject/README shaped for plugin dev. These are dead artifacts on disk awaiting sprint-03's repo-realignment job (delete per *remove don't shim* — not preserved, not shimmed).

**Lesson reinforced:** two SRS pivots in two days; the cheap-abort discipline (kickoffs frozen but jobs not started → withdraw cleanly; PROJECT_STATE halt-note made resume/abort decidable in minutes) is working as designed. Continue keeping sprint scaffolding lightweight until an SRS revision survives a sprint.
