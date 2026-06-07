# Sprint 07: SFINCS engine v0 + WorldPop default flip (SRS v0.3 M5)

**Status:** planned
**Opened:** 2026-06-06
**Closed:** —
**SRS milestones covered:** M5 (SFINCS solver containerization + first real engine integration; `model_flood_scenario` workflow composing the M4 atomic-tool substrate into a working hazard-modeling pipeline).

## Goal

Land the **first real engine integration** end-to-end: SFINCS flood solver deployed as a Cloud Run Job (per FR-CE-1/2/3); composed into the `model_flood_scenario` workflow that chains M4 atomic tools (geocode → DEM/landcover/buildings/precip return-period → SFINCS run → flood-depth COG → rendered layer); demonstrated with "Hurricane Ian flood on Fort Myers" as the M5 acceptance demo. Plus the WorldPop default flip from v0.3.16 Appendix F.1 (unblocks the M4 Fort Myers demo end-to-end with zero key).

Sprint-07 takes the M4 substrate (atomic-tool registry + cache shim + real envelope emission) and proves it can carry a real solver run. NFR-P-4 (15 min for ≤200km² at 30m) becomes load-bearing for the first time. The `run_solver` + `wait_for_completion` atomic tools exercise job-0035's `update_progress` opt-in seam for the first time — real long-running progress emission through the agent's WS pipeline-state envelopes.

**Engine deployment strategy: LAZY PER-MILESTONE (user-confirmed 2026-06-06).** Only SFINCS gets containerized + deployed in sprint-07. MODFLOW / HEC-HMS / TELEMAC / etc. (§2.3 deferred engines) wait until their respective milestone sprints. Avoids 12 idle Cloud Run Jobs (~$120/mo) + 12 image bakes ahead of need.

**No Discovery-First lane in this sprint** (§F.2 — `hazard_catalog_search` / `fetch_public_hazard_layer` / `summarize_layer_in_bbox`). Confirmed for sprint-08 per user direction.

**No `request_secret` UX** (§F.3 deferred indefinitely per user direction).

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|---|---|---|---|---|
| job-0037-engine-20260606 | engine | **Mini early job:** flip `fetch_population` default to WorldPop per Appendix F.1; ACS opt-in via `dataset="acs_2022"`; re-run Fort Myers demo to verify zero-key Tier-1 path works end-to-end | — | planned |
| job-0038-engine-20260606 | engine | **OQ-4 HydroMT integration depth decision** — research SFINCS + HydroMT setup pattern; propose depth (full HydroMT reliance vs custom config builders); land decision in a new `docs/decisions/oq-4-hydromt-depth.md` (Sonnet — research/summarize) | — | planned |
| job-0039-engine-20260606 | engine | 3 new fetcher atomic tools — `fetch_landcover` (NLCD via MRLC, ESA WorldCover via STAC; `static-30d`), `fetch_river_geometry` (NHDPlus HR; `static-30d`), `lookup_precip_return_period` (NOAA Atlas 14 PFDS; `static-30d`); all Tier-1 per Appendix F.1 | job-0037, job-0038 | planned |
| job-0040-infra-20260606 | infra | SFINCS solver container — Dockerfile (Deltares simvia base image or hand-built) + Cloud Build + Artifact Registry + Cloud Run Job `grace-2-sfincs-solver` + Cloud Workflows orchestration step + `sfincs-runtime@grace-2-hazard-prod` SA with bucket-scoped objectAdmin on cache + runs buckets + workflow-invoker IAM. Live `tofu apply` + smoke run | — | planned |
| job-0041-agent-20260606 | agent | `run_solver(solver, model_setup_uri, compute_class)` + `wait_for_completion(handle)` atomic tools wrapping Cloud Workflows execution; emits real `pipeline-state` progress updates via job-0035's `PipelineEmitter.update_progress` opt-in; reuses M1 cancel chain | job-0040 | planned |
| job-0042-engine-20260606 | engine | `model_flood_scenario(bbox, event_id?, return_period_yr?) → AssessmentEnvelope` workflow composing geocode → fetch_dem → fetch_landcover → fetch_river_geometry → lookup_precip_return_period → HydroMT model build → run_solver(sfincs) → postprocess_flood → AssessmentEnvelope. Closes OQ-36-QGIS-PROCESS-DEMO-CHAIN | job-0039, job-0041 | planned |
| job-0043-testing-20260606 | testing | M5 acceptance: end-to-end "Hurricane Ian flood on Fort Myers" demo through the deployed substrate. Real SFINCS run on Cloud Run; real flood-depth COG written to cache + runs buckets; real envelope rendered via QGIS Server. Per-tool cache verification (FR-DC-4 dedup); NFR-P-4 timing capture (target ≤15 min for ≤200 km²); full M1+M2+M3+M4+M5 regression. Closes sprint-07 | job-0042 | planned |

## Execution order

```
stage A (parallel, file-disjoint):
  job-0037-engine   (WorldPop default flip — tiny mechanical edit)
  job-0038-engine   (OQ-4 HydroMT depth decision — research/summarize)
  job-0040-infra    (SFINCS container + Cloud Run Job + IAM — heavy infra)

stage B:
  job-0039-engine   (3 new fetcher tools)
  ← gated on 0037 (WorldPop edit closed; avoids data_fetch.py collision)
                  + 0038 (HydroMT depth decision lands in docs/decisions/)

stage C:
  job-0041-agent    (run_solver + wait_for_completion + progress emission)
  ← gated on 0040 (SFINCS Cloud Run Job + Workflows live so submission has a target)

stage D:
  job-0042-engine   (model_flood_scenario workflow composes the chain)
  ← gated on 0039 (3 fetcher tools) + 0041 (run_solver)

stage E:
  job-0043-testing  (M5 acceptance + sprint close)
  ← gated on 0042 approved
```

## Exit criteria

- [ ] **WorldPop is the `fetch_population` Tier-1 default** per Appendix F.1; ACS opt-in via `dataset="acs_2022"`; Fort Myers demo (WorldPop branch) passes end-to-end with no API key.
- [ ] OQ-4 HydroMT depth decision landed in `docs/decisions/oq-4-hydromt-depth.md` with cited tradeoffs; sprint-07 fetchers + workflow conform to the chosen depth.
- [ ] 3 new fetcher atomic tools registered (`fetch_landcover`, `fetch_river_geometry`, `lookup_precip_return_period`) — all `static-30d`, all Tier-1, all route through cache shim per FR-CE-8. Tool registry shows 11 tools on `--startup-only` (M4's 8 + 3 new).
- [ ] SFINCS solver container live: `gcloud run jobs describe grace-2-sfincs-solver` returns success; `sfincs-runtime` SA exists with bucket-scoped IAM (mirrors job-0021 / job-0031 zero-project-grants discipline); Cloud Workflows step invokes the job and waits for completion.
- [ ] `run_solver` + `wait_for_completion` atomic tools registered; `update_progress` opt-in emits real `pipeline-state` envelopes (≥3 progress updates during a real SFINCS run, captured in evidence). Cancel chain reaches the running solver within the FR-AS-6 / NFR-R-3 30s budget.
- [ ] `model_flood_scenario(...)` workflow returns a populated `AssessmentEnvelope` (Appendix B.4 Flood subtype) with `flood_depth` LayerURI pointing at a real COG in the `runs` bucket.
- [ ] M5 acceptance: Hurricane Ian / Fort Myers demo end-to-end PASS. Flood-depth layer renders on the web client via QGIS Server. Captured screenshot under sprint-07 evidence dir.
- [ ] NFR-P-4 timing captured (real wall-clock for ≤200 km² ≤30m run); recorded honestly with environment context per testing.md NFR discipline. Target ≤15 min.
- [ ] FR-DC-4 dedup verified: re-running the demo within 30 minutes hits cache for the 4 fetchers; within 30 days hits cache for all `static-30d` fetchers.
- [ ] `make test` + `make test-m2` + `make test-m3` + `make test-m4` + new `make test-m5` all green; baselines preserved.
- [ ] No edits to FROZEN paths per AGENTS.md.

**Out of scope (deferred, do not bundle in sprint-07):**
- Discovery-First lane atomic tools (§F.2) — sprint-08.
- §F.3 `request_secret` Secrets UX — deferred indefinitely per user direction.
- Census API key wiring (OQ-36-CENSUS-API-KEY-REQUIRED) — orchestrator-direct one-shot after user provisions key off-band; not a job.
- v0.3.17 SRS housekeeping pass (6 carry-forward OQs: OQ-W-26, OQ-INFRA-31-FR-DC-1, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP, OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION, OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY, OQ-36-CACHE-REGRESSION-FAKE-FIDELITY) — orchestrator-direct at sprint-07 close.
- Other §2.3 deferred engines (MODFLOW, HEC-HMS, TELEMAC, ParFlow, etc.) — lazy per-milestone deploy; await their respective milestone sprints.

## Retrospective

_Filled at close._
