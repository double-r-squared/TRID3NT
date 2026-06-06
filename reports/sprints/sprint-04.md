# Sprint 04: QGIS Server in cloud + PyQGIS worker prototype (SRS v0.3 M2)

**Status:** planned
**Opened:** 2026-06-05
**Closed:** —
**SRS milestones covered:** M2 (QGIS Server in cloud + PyQGIS worker prototype)

## Goal

At the end of this sprint a live QGIS Server Cloud Run service answers `GetCapabilities` + `GetMap` against a sample `.qgs` it reads from GCS via `/vsigs/`, rendering a Tier-A-style basemap layer styled by the M2 QML preset stub; a containerized PyQGIS worker Cloud Run Job demonstrates the canonical FR-QS-6 round-trip end-to-end (pull `.qgs` from GCS → mutate via PyQGIS → write back → Pub/Sub notify); the canonical sample `.qgs` and first preset stub are committed to source under `services/workers/pyqgis/sample_project/` and `styles/`; three new GCS buckets (`grace-2-hazard-prod-qgs`/`-cog`/`-fgb`) hold the .qgs/COG/FlatGeobuf payloads with public access prevented; the `grace2` conda env is recreated on the Debian 13 dev box for local worker iteration; and the M1 regression suite (114 tests) stays green. Nothing else changes — no agent integration, no web tile consumption (deferred to M3), no SFINCS workflow (deferred to M5), no `packages/contracts/`/`services/agent/`/`web/` edits.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|
| job-0018-infra-20260605 | infra | QGIS Server Cloud Run service + GCS .qgs/COG/FGB buckets + Pub/Sub notify topic | — | **approved** |
| job-0019-engine | engine | Sample `.qgs` + `styles/basemap.qml` preset stub authored and uploaded to GCS .qgs bucket | 0018 | **approved** |
| job-0020-engine | engine | PyQGIS worker code: `worker_round_trip(qgs_uri, layer_to_add)` (read `/vsigs/` → mutate → write back → Pub/Sub publish) | 0018, 0019 | planned |
| job-0021-infra | infra | PyQGIS worker container Dockerfile + image build/push + Cloud Run Job deployment | 0018, 0020, 0024 | planned |
| job-0022-infra | infra | `grace2` conda env recreation on Linux Debian via conda-forge (QGIS 3.40.3 + Python 3.12, dead-dep strip) | — | **approved** |
| job-0023-testing | testing | M2 acceptance: GetCapabilities + WMS GetMap + worker round-trip transcript + M1 regression (114 tests) green | 0018, 0019, 0020, 0021, 0024 | planned |
| job-0024-infra-20260605 | infra | QGIS Server `/vsigs/` access fix (GDAL VSI env vars or gcsfuse) + QML preset bake into image — **inserted mid-sprint after job-0019 surfaced OQ-19A** | 0018, 0019 | planned |

(Job IDs are surfaced as `job-00NN-<specialist>-20260605` when scaffolded; the dated suffix is omitted in the table above for column width.)

## Execution order

```
stage A (parallel):  job-0018 (QGIS Server + buckets + Pub/Sub)   job-0022 (grace2 conda env)
stage B:             job-0019 (sample .qgs + basemap.qml)         ← gated on 0018 (bucket exists)
stage C:             job-0020 (PyQGIS worker code)                ← gated on 0018 + 0019
stage D:             job-0021 (worker container + Cloud Run Job)  ← gated on 0018 + 0020
stage E:             job-0023 (M2 acceptance suite + M1 regression rerun)  ← gated on 0018 + 0019 + 0020 + 0021
```

Each stage edge is an in-workflow adversarial review per AGENTS.md "Execution Model — Workflows." One revision round per job; second failure marks the job `blocked` and halts dependents. job-0018 and job-0022 are parallel (disjoint file ownership: `infra/qgis-server/`, `infra/worker/`, `infra/*.tf` vs `infra/conda/environment.yml`). 0019, 0020, 0021 are serial behind 0018 because each consumes a substrate the prior job produced. 0023 closes the sprint.

## Exit criteria

Checkable statements. The sprint closes only when every one is verified with cited evidence by job ID.

| # | Criterion | Evidence to cite |
|---|---|---|
| 1 | QGIS Server Cloud Run service answers `GetCapabilities` against `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` via `/vsigs/`; image is the pinned `qgis/qgis-server` digest with GRASS/SAGA/processing baked in; `qgis_process` CLI exposed; `--min-instances=0`; stateless per NFR-R-4 | job-0018 audit + job-0023 acceptance row 1 (verbatim curl transcript) |
| 2 | Three GCS buckets exist (`grace-2-hazard-prod-qgs`/`-cog`/`-fgb`), uniform BLA + PAP enforced, labeled `project=grace-2 env=dev sprint=04`, service-account scoped (no public access); Pub/Sub topic `grace-2-worker-events` exists with no subscriber; all in committed OpenTofu under `infra/` with `tofu plan: No changes` | job-0018 audit + job-0023 invariant check (gcloud transcript) |
| 3 | `services/workers/pyqgis/sample_project/grace2-sample.qgs` round-trips through `QgsProject.read()/write()` cleanly and is uploaded to `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`; `styles/basemap.qml` matches the basemap layer name in the sample project; project provenance documented in `services/workers/pyqgis/sample_project/README.md` | job-0019 audit + job-0023 acceptance row 3 (PNG artifact at `tests/m2/artifacts/sample-getmap.png`, non-blank) |
| 4 | `services/workers/pyqgis/worker.py` implements `worker_round_trip(qgs_uri, layer_to_add) -> WorkerResult` with FR-TA-3-complete docstring; reads via `/vsigs/`; mutates by appending a second styled layer (proves `update_project_layers` + `apply_style_preset` codepath); writes back; publishes a typed completion message to `grace-2-worker-events`; ZERO LLM calls in the function body (invariant 2) | job-0020 audit (unit-test transcript) + job-0023 acceptance row 4 (Cloud Run Job execution log) |
| 5 | PyQGIS worker container builds `linux/amd64`-only, pushes to Artifact Registry, deploys as Cloud Run Job `grace-2-pyqgis-worker` with `--max-retries=0 --task-timeout=15m --parallelism=1`, runs as dedicated worker SA with `roles/storage.objectAdmin` on the .qgs bucket + `roles/pubsub.publisher` on the topic (Workload Identity bound, no embedded creds); committed OpenTofu | job-0021 audit (build transcript + `gcloud run jobs execute` transcript) |
| 6 | `infra/conda/environment.yml` recreates the `grace2` env on Linux Debian 13 from conda-forge with `qgis=3.40.3` + `python=3.12` + `google-cloud-storage` + `google-cloud-pubsub`; dead deps from v0.2 stripped (no `boto3`, no `strands`, no provider-abstraction packages); `python -c "from qgis.core import QgsProject"` succeeds in the env; `infra/README.md` documents the bootstrap | job-0022 audit (conda create transcript + import check) |
| 7 | `make test` green: 91 contracts + 23 acceptance = 114 M1 tests pass unchanged; `make test-m2` green: GetCapabilities + GetMap PNG + worker round-trip transcript + invariant verification (PAP enforced, worker SA roles minimal) | job-0023 audit (full pytest transcript + artifact paths) |

## Retrospective

_Filled at close._
