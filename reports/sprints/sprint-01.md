# Sprint 01: Foundations + canvas hello-world + canvas IPC

**Status:** aborted
**Opened:** 2026-06-04
**Closed:** 2026-06-04 (aborted)
**SRS milestones covered:** SRS v0.1 M1 (canvas hello-world), M2 (canvas IPC) — superseded by SRS v0.2 before any job started

## Goal

At the end of this sprint a developer (or agent) on this Mac can: create the blessed `grace2` conda env from `environment.yml`; run `make run` to get a PyQGIS window with an OSM basemap, pan/zoom, and coordinate readout; drive that canvas from a second process with JSON commands over a local socket (`load_layer`, `zoom_to_layer`, `set_layer_opacity`) per a versioned contract; and run `make test` to verify all of it headlessly. The shared contracts for canvas commands, WebSocket messages, and pipeline state exist as v0 stubs ready for Sprint 2's agent service.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|
| job-0001-infra-20260604 | infra | Dev environment (conda-forge PyQGIS) + repo scaffold | — | created |
| job-0002-schema-20260604 | schema | Contracts v0: canvas commands, WS envelope, pipeline state | — | created |
| job-0003-desktop-ui-20260604 | desktop-ui | M1: canvas hello-world (window, OSM, pan/zoom) | 0001 | created |
| job-0004-desktop-ui-20260604 | desktop-ui | M2: canvas IPC (JSON commands over local socket) | 0002, 0003 | created |
| job-0005-testing-20260604 | testing | Smoke harness + sprint acceptance verification | 0004 | created |

## Execution order

```
stage A (parallel):  job-0001 (long pole: conda install)   job-0002 (venv-fallback verification)
stage B:             job-0003   (gated on 0001 review pass)
stage C:             job-0004   (gated on 0002 + 0003 review pass)
stage D:             job-0005   (gated on 0004 review pass; re-verifies 0002 in the real env)
```

Each gate is an in-workflow adversarial review per AGENTS.md § "Execution Model — Workflows". One revision round per job; second failure blocks the job and its dependents.

## Exit criteria

1. `conda run -n grace2 python -c "from qgis.core import Qgis; print(Qgis.QGIS_VERSION)"` prints a QGIS version (evidence: job-0001 report)
2. Canvas command, WebSocket envelope, and pipeline state contracts importable from `grace2.contracts` with generated JSON Schemas in `docs/contracts/` (evidence: job-0002 report)
3. `make run` opens the app on macOS showing rendered OSM tiles with working pan/zoom — screenshot evidence (job-0003 report)
4. A second process loads a layer, zooms to it, and sets opacity on the running canvas via the local socket using contract-conformant frames; a bogus URI yields a structured error without crashing the app — transcript + screenshot evidence (job-0004 report)
5. `make test` passes in the `grace2` env, including the headless canvas smoke test, the IPC round-trip with negative control, and the contract suite (evidence: job-0005 report)

## Retrospective

Aborted same-day: SRS v0.2 (2026-06-04) pivoted the product from a standalone PyQGIS desktop app to a QGIS plugin built on a vendored GeoAgent snapshot, invalidating both milestone targets (embedded canvas, canvas IPC). No job had left `created`; all five (job-0001 … job-0005) withdrawn without work lost. What carries forward into sprint-02: the conda-forge dev-environment decision and most of the infra job shape, the contracts-first sequencing, and the parallel-then-gate execution plan. Lesson recorded: scaffold-then-pivot was cheap precisely because no kickoff had been handed to a specialist — keep planning lightweight until the SRS settles.
