# Sprint 06: Agent tools + atomic-tool starter set (SRS v0.3 M4)

**Status:** planned
**Opened:** 2026-06-06
**Closed:** —
**SRS milestones covered:** M4 (Agent tool registry + 7 atomic tools enabling the "Fort Myers below 3m elevation" demo end-to-end against the live agent).

## Goal

Stand up the **agent-side tool registry** in `services/agent/tools/` and ship 7 atomic tools that together demonstrate a real end-to-end hazard-modeling query through the deployed substrate. The 7 tools (`fetch_dem`, `fetch_buildings`, `fetch_population`, `geocode_location`, `list_qgis_algorithms`, `describe_qgis_algorithm`, plus the `mongo_query` / `qgis_process` registry pass-throughs) are scoped to enable a single concrete demo: *"what's the population of Fort Myers below 3m elevation?"* — a chain that exercises geocoding, DEM fetch, population fetch, QGIS reclassification, and zonal statistics, returning a populated envelope and a rendered map layer.

M4 is also where the **caching architecture from v0.3.15 (Decision O + FR-DC-1..6 + FR-CE-8) becomes load-bearing**: every external-API atomic tool registers a TTL class at definition time and routes through the shared cache shim. The two pre-flight jobs (0030 schema + 0031 infra) put the contract surface and the GCS bucket in place so the data-fetcher jobs can register cleanly.

M4 is strictly atomic-tool work plus the agent-side pipeline-state emission that sprint-05 deferred to M4. **No engine work, no Pelicun, no TELEMAC, no new web client surface.** The 91 contracts + 30 protocol/integration/M2 + 10 M3 regression baselines remain green.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|---|---|---|---|---|
| job-0030-schema-20260606 | schema | Appendix D.6 `PipelineStepSummary` extension (`progress_percent`/`error_code`/`error_message`) + FR-DC TTL-class metadata field on FunctionTool registration; pydantic contract bump; JSON Schema re-export | — | planned |
| job-0031-infra-20260606 | infra | Provision `gs://grace-2-hazard-prod-cache/` bucket via OpenTofu + 4 GCS Object Lifecycle Management rules per FR-DC-5 (one per TTL class) + IAM for agent-runtime SA | — | planned |
| job-0032-agent-20260606 | agent | Tool registry skeleton (`services/agent/tools/__init__.py`); shared cache shim implementing FR-DC-3 read-through / write-on-miss / content-addressed keys; `mongo_query` + `qgis_process` registry pass-throughs | job-0030, job-0031 | planned |
| job-0033-engine-20260606 | engine | 4 data-fetch atomic tools — `fetch_dem` (USGS 3DEP via py3dep, `static-30d`), `fetch_buildings` (MS Building Footprints FlatGeobuf, `static-30d`), `fetch_population` (WorldPop or US Census, `static-30d`), `geocode_location` (Nominatim/Mapbox, `dynamic-1h`) | job-0032 | planned |
| job-0034-engine-20260606 | engine | 2 QGIS discovery atomic tools — `list_qgis_algorithms` + `describe_qgis_algorithm` wrapping `qgis_process list` / `qgis_process help` against the deployed PyQGIS worker (operational from sprint-04 job-0021) | job-0032 | planned |
| job-0035-agent-20260606 | agent | Real `pipeline-state` + `session-state.loaded_layers` emission from the agent service using the D.6 fields from job-0030; closes OQ-T-28-SIM-WS-BOUNDARY (M3 tests rewrite to drive real agent emission) | job-0030 | planned |
| job-0036-testing-20260606 | testing | M4 acceptance: end-to-end "Fort Myers below 3m" demo + per-tool cache hit/miss verification + dedup guarantee (FR-DC-4) + uncacheable enumeration (FR-DC-6) honored + full M1+M2+M3+M4 regression. Closes sprint-06. | job-0033, job-0034, job-0035 | planned |

## Execution order

```
stage A (parallel):  job-0030-schema    (PipelineStepSummary fields + TTL-class metadata field)
                     job-0031-infra     (cache bucket + lifecycle rules)
                     ─ disjoint file ownership ─

stage B:             job-0032-agent     (tool registry + cache shim + registry pass-throughs)
                     ← gated on 0030 (TTL-class field) + 0031 (cache bucket)

stage C (parallel):  job-0033-engine    (4 data-fetch atomic tools)
                     job-0034-engine    (2 QGIS discovery atomic tools)
                     job-0035-agent     (real pipeline-state + session-state emission)
                     ─ disjoint file ownership ─

stage D:             job-0036-testing   (M4 acceptance + sprint close)
                     ← gated on 0033 + 0034 + 0035 approved
```

## Exit criteria

- [ ] Appendix D.6 `PipelineStepSummary` carries `progress_percent: int | None`, `error_code: str | None`, `error_message: str | None`; `grace2-contracts` minor bump; 91+ contracts tests still green.
- [ ] ADK FunctionTool registration validates that every external-API tool declares exactly one of `static-30d` / `semi-static-7d` / `dynamic-1h` / `live-no-cache` (per FR-AS-3 + FR-DC-2); tool-registration fails fast if class is missing.
- [ ] `gs://grace-2-hazard-prod-cache/` bucket exists with 4 lifecycle rules (one per TTL class day count: 30, 7, 1, 0); rules tied to `customTime` per FR-DC-5; agent-runtime SA has `objectAdmin` on the bucket.
- [ ] Cache shim implements FR-DC-3 read-through + write-on-miss + content-addressed keys (`sha256(source_id || canonicalized_params || ttl_bucket_vintage)`); deduplication guarantee (FR-DC-4) verified with parallel-write test; uncacheable enumeration (FR-DC-6) honored.
- [ ] All 7 atomic tools registered + invocable through the agent; ADK FunctionTool docstrings include "Use this when / Do NOT use this for" per FR-TA-3.
- [ ] **End-to-end demo:** user sends `"what's the population of Fort Myers below 3m elevation?"` → agent chain: `geocode_location` → `fetch_dem(bbox, 10m)` → `fetch_population(bbox)` → `qgis_process('native:reclassifybytable', ...)` → `qgis_process('native:zonalstatistics', ...)` → `ImpactEnvelope` returned + map layer rendered on the web client. Single screenshot of the result panel committed under sprint-06 evidence dir.
- [ ] Agent emits real `pipeline-state` envelopes (not the M3 dev-injection seam) with the new D.6 fields populated; M3 `test_pipeline_strip` rewritten to drive the live emission path.
- [ ] Cache verification: re-running the demo within 30 minutes (`dynamic-1h` bucket overlap) hits cache for `geocode_location`; re-running within 30 days hits cache for `fetch_dem` / `fetch_buildings` / `fetch_population`. Verified by GCS object timestamps + agent service logs.
- [ ] `make test` green: 91 contracts + 30 protocol/integration/M2 + 10 M3 + new M4 = ~145+ invocations baseline preserved.
- [ ] No edits to FROZEN paths per AGENTS.md (`reports/complete/**`, `docs/SRS_v0.3.md` directly — edit `docs/srs/<section>.md` and `make srs`).

## Retrospective

_Filled at close_
