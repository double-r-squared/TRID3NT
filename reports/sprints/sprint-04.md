# Sprint 04: QGIS Server in cloud + PyQGIS worker prototype (SRS v0.3 M2)

**Status:** complete
**Opened:** 2026-06-05
**Closed:** 2026-06-06
**SRS milestones covered:** M2 (QGIS Server in cloud + PyQGIS worker prototype)

## Goal

At the end of this sprint a live QGIS Server Cloud Run service answers `GetCapabilities` + `GetMap` against a sample `.qgs` it reads from GCS via `/vsigs/`, rendering a Tier-A-style basemap layer styled by the M2 QML preset stub; a containerized PyQGIS worker Cloud Run Job demonstrates the canonical FR-QS-6 round-trip end-to-end (pull `.qgs` from GCS → mutate via PyQGIS → write back → Pub/Sub notify); the canonical sample `.qgs` and first preset stub are committed to source under `services/workers/pyqgis/sample_project/` and `styles/`; three new GCS buckets (`grace-2-hazard-prod-qgs`/`-cog`/`-fgb`) hold the .qgs/COG/FlatGeobuf payloads with public access prevented; the `grace2` conda env is recreated on the Debian 13 dev box for local worker iteration; and the M1 regression suite (114 tests) stays green. Nothing else changes — no agent integration, no web tile consumption (deferred to M3), no SFINCS workflow (deferred to M5), no `packages/contracts/`/`services/agent/`/`web/` edits.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|
| job-0018-infra-20260605 | infra | QGIS Server Cloud Run service + GCS .qgs/COG/FGB buckets + Pub/Sub notify topic | — | **approved** |
| job-0019-engine | engine | Sample `.qgs` + `styles/basemap.qml` preset stub authored and uploaded to GCS .qgs bucket | 0018 | **approved** |
| job-0020-engine | engine | PyQGIS worker code: `worker_round_trip(qgs_uri, layer_to_add)` (read `/vsigs/` → mutate → write back → Pub/Sub publish) | 0018, 0019 | **approved** |
| job-0021-infra | infra | PyQGIS worker container Dockerfile + image build/push + Cloud Run Job deployment | 0018, 0020, 0024 | **approved** |
| job-0022-infra | infra | `grace2` conda env recreation on Linux Debian via conda-forge (QGIS 3.40.3 + Python 3.12, dead-dep strip) | — | **approved** |
| job-0023-testing | testing | M2 acceptance: GetCapabilities + WMS GetMap + worker round-trip transcript + M1 regression (114 tests) green | 0018, 0019, 0020, 0021, 0024 | **approved** |
| job-0024-infra-20260605 | infra | QGIS Server `/vsigs/` access fix (GDAL VSI env vars or gcsfuse) + QML preset bake into image — **inserted mid-sprint after job-0019 surfaced OQ-19A** | 0018, 0019 | **approved** |

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

## Exit criteria — verification

All seven exit criteria **pass** with cited live evidence per row.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | QGIS Server Cloud Run service answers GetCapabilities; image digest-pinned; min-instances=0 | **pass** | job-0018 audit + job-0024 audit (image bumped to `@sha256:a703476…` after `/vsigs/`→`/mnt/qgs` pivot); `tests/m2/test_qgis_server_wms.py::test_getcapabilities_returns_valid_xml` returns valid `<WMS_Capabilities>` XML naming `<Name>basemap-osm-conus</Name>`; artifact `tests/m2/artifacts/getcapabilities.xml` (5.9 KB) |
| 2 | Three buckets (UBLA + PAP + SA-scoped); Pub/Sub topic; OpenTofu clean | **pass** | job-0018 audit; `tests/m2/test_iac_clean.py::test_no_public_buckets` asserts PAP=enforced + UBLA=True on all 3 buckets + zero `allUsers`/`allAuthenticatedUsers` IAM; `test_tofu_plan_clean` exit 0 (only documented OQ-F cosmetic carry-forward) |
| 3 | Sample .qgs round-trips + uploaded to GCS; basemap.qml matches layer name | **pass** | job-0019 audit + job-0024 audit (QML baked at `/etc/qgis/styles/`); `test_getmap_returns_png` returns 332 KB PNG at 800×400 (magic bytes verified); artifact `tests/m2/artifacts/sample-getmap.png` |
| 4 | `worker_round_trip` implements read→mutate→writeback→publish; zero LLM calls | **pass** | job-0020 audit (URI-agnostic dispatch via `_parse_qgs_uri`; LLM-grep zero hits); `tests/m2/test_pyqgis_worker_roundtrip.py` × 3 tests pass on Cloud Run Job execution `49c6c439`; envelope captured (`status=ok`, `qgs_version=3.44.11-Solothurn`) |
| 5 | Worker container linux/amd64 + Cloud Run Job + worker SA (bucket-scoped objectAdmin + topic-scoped publisher) | **pass** | job-0021 audit (image `@sha256:fffd7e0f…`; ZERO project-level IAM grants verified live); `test_worker_job_execute_succeeds` Cloud Run Job execution `EXECUTION_SUCCEEDED` |
| 6 | grace2 conda env on Debian via conda-forge; dead deps stripped | **pass** | job-0022 audit (`Qgis.QGIS_VERSION = 3.40.3-Bratislava`; google-cloud-{storage,pubsub} import OK; zero `boto3`/`strands`/`ollama` in active deps) |
| 7 | `make test` green M1 (114) + `make test-m2` green | **pass** | `make test` = 121/121 green in 247 s (91 contracts + 23 M1 acceptance + 7 M2 acceptance); job-0023 transcript |

**Verdict: M2 achieved (7 of 7 pass).** No qualifications. Sprint-04 closes clean.

## Retrospective

**What shipped (the bones of M2):**

- **Live QGIS Server Cloud Run service** at `https://grace-2-qgis-server-425352658356.us-central1.run.app` rendering the sample CONUS basemap from GCS via WMS; image digest-pinned (`@sha256:a703476…`) with `qgis_process` CLI baked in; min-instances=0; `qgis-server-runtime` SA scoped to `roles/storage.objectViewer` on buckets only.
- **Three private GCS buckets** (`grace-2-hazard-prod-qgs`/`-cog`/`-fgb`) all UBLA + PAP-enforced + lifecycle + SA-scoped IAM; zero public exposure verified by live test.
- **Pub/Sub topic `grace-2-worker-events`** provisioned (FR-QS-6 step 5 substrate).
- **Artifact Registry repo `grace-2-containers`** holding both the QGIS Server and worker images.
- **Sample `.qgs` + QML preset** authored programmatically via PyQGIS in the `grace2` env; layer `basemap-osm-conus` + OSM XYZ tile source; preset baked into the QGIS Server image at `/etc/qgis/styles/basemap.qml`.
- **PyQGIS worker code** (`services/workers/pyqgis/`): `worker_round_trip(qgs_uri, layer_to_add) → WorkerResult` with URI-agnostic dispatch (`/vsigs/`, `gs://`, `/mnt/qgs/`, local path); reads `.qgs`, appends layer, writes back, publishes typed completion envelope; zero LLM packages imported (Invariant 2 verified by grep).
- **PyQGIS worker Cloud Run Job `grace-2-pyqgis-worker`** containerized from the same QGIS Server base image (version parity); image `@sha256:fffd7e0f…`; writable `/mnt/qgs` mount; worker SA `pyqgis-worker-runtime` with **bucket-scoped** `objectAdmin` + **topic-scoped** `publisher` and **zero project-level grants** (verified live via `gcloud projects get-iam-policy`).
- **Local `grace2` conda env on Debian 13** via conda-forge: QGIS 3.40.3-Bratislava + Python 3.12.13 + GDAL 3.10.2 + google-cloud-{storage,pubsub} 3.11/2.38 + pytest 9. Reproducible from `infra/conda/environment.yml`. Dead deps (boto3/strands/AWS/Ollama/LLMProvider) stripped — verified by grep.
- **Live end-to-end round-trip**: `gcloud run jobs execute` against `grace-2-pyqgis-worker` succeeded; mutated `.qgs` downloaded from GCS has 2 layers; Pub/Sub envelope captured and decoded.
- **121-test acceptance suite**: 91 contracts + 23 M1 acceptance (preserved) + 7 M2 acceptance, exit 0 in 247 s. Real-substrate live verification only (no mocks except the M1 Gemini-adapter seam, which M2 doesn't touch).

**Decisions that landed (orchestrator surfaced, validated through specialist work):**

- **WMS URL contract change `/vsigs/` → `/mnt/qgs/` for `.qgs` files** (job-0024). Discovered mid-sprint: QGIS Server uses Qt `QFile` to load `.qgs`, not GDAL — so `/vsigs/` doesn't work for project-file load. Pivoted to Cloud Run gen2 native GCS volume mount (`read_only=true` on QGIS Server; `read_only=false` on the worker — Invariant 4 enforced at runtime). GDAL VSI env vars kept in place because they DO still help for `/vsigs/` layer-data references inside `.qgs` (COG/FlatGeobuf). **Surface to user as FR-QS-2 SRS amendment proposal.**
- **Cloud Run gen2 native GCS mount over gcsfuse-in-image** (job-0024). No PID-1 wrapper, no `/etc/fuse.conf`, no FUSE-device permission — fewer failure surfaces. Trade-off (gen2-specific) is acceptable since the entire stack is on Cloud Run by Decision E.
- **QGIS Server `read_only=true` + Worker `read_only=false` mount asymmetry** (job-0021): preserves Invariant 4 at runtime, not just by convention. QGIS Server *cannot* write `.qgs` even if the binary had a CVE; only the worker can.
- **Same-image-base for QGIS Server and Worker** (job-0021): eliminates QGIS version drift between writes and reads. Image is heavier than necessary (~3 GB; OQ-21B) but version parity wins over size for M2.
- **Worker SA bucket-scoped + topic-scoped only; zero project grants** (job-0021): minimum-privilege verified live; security boundary is the resource scope, not split roles.
- **Miniforge3 over Mambaforge (deprecated upstream)** for the grace2 conda env (job-0022). Conda-forge-only posture matches NFR-L licensing.
- **`qgis_process` CLI baked into QGIS Server image** (job-0018): enables future FR-AS-9 Level 1a algorithm discovery from the agent. Adds ~200 MB; bake-here-now, revisit if image bloat becomes operational concern.
- **`tofu plan` targeted (M2-resources-only)** for the acceptance test (job-0023): full plan requires the Atlas API key ritual; targeted plan honors the kickoff's documented OQ-F carry-forward allowance.

**What worked:**

- **Diagnose-before-fix paid dividends twice**: (1) job-0024's path-(c) GDAL VSI env vars were tried first as a 5-minute diagnostic, failed cleanly, the specialist explained *why* (Qt vs GDAL) before pivoting — exemplary discipline; (2) job-0021's first Cloud Run Job execution SEGV'd at `QgsApplication` ctor; diagnosed as missing `QT_QPA_PLATFORM=offscreen`, baked into Dockerfile, second build clean.
- **The adversarial in-workflow reviewer caught real issues every job**: digest-vs-`:latest`, README budget itemization, sidecar zip leftover, layer-ordering non-determinism. None would have been caught by self-review.
- **Mid-sprint scope addition handled cleanly**: job-0019 surfaced OQ-19A as a substrate gap (not an engine defect); orchestrator opened job-0024 inline (counter 23→24) without blocking job-0020 (which is independent). The sprint completed with the new job folded in.
- **Layer-name canonicalization (`osm-basemap` → `basemap-osm-conus`)** caught at the job-0019 audit and surgically applied to downstream kickoffs (0020 + 0023) before handoff — the frozen-after-handoff rule held.
- **Real-substrate test discipline**: every acceptance test ran against the real Cloud Run service / real GCS / real Cloud Run Job / real Pub/Sub topic. No mocks. 121 tests in 247 s.
- **Image digest pinning + IaC + Cloud Build all formed a closed loop**: every container image referenced by Cloud Run is sha256-pinned in OpenTofu; rebuilds force explicit digest bumps.

**What surprised:**

- **`/vsigs/` doesn't work for `.qgs` project files** (only for layer data inside them) — a real SRS misconception we caught mid-sprint. The original FR-QS-2 wording was reasonable but inaccurate; amendment pending.
- **QGIS Server container 3.44 vs grace2 conda env 3.40** drifted (different `conda-forge` vs `qgis/qgis-server` Docker Hub release cadences). Captured live in the published envelope as `qgs_version` field for traceability; revisit at M5 if it bites.
- **First Cloud Run Job execution SEGV'd in Qt** because there's no display; `QT_QPA_PLATFORM=offscreen` was needed. Standard PyQGIS-headless gotcha, caught fast.
- **Three Pub/Sub tests can share one Cloud Run Job execution** (saves cold-start cost); the testing specialist figured this out and structured tests around a single fixture-managed execution.
- **The job-0020 worker code accepted `/mnt/qgs/` natively without modification** because the specialist anticipated URI dispatch — the job-0024 contract change was a zero-code-change update on the worker side.

**Decisions surfaced to user for landing at this sprint close** (carry-forward + new):

- **(NEW) FR-QS-2 SRS amendment**: "`.qgs` files load via a Cloud Run GCS volume mount at `/mnt/qgs/`; layer-data references inside `.qgs` continue to use `/vsigs/` with GDAL VSI auth via `CPL_MACHINE_IS_GCE`/`CPL_GS_USE_INSTANCE_PROFILE` env vars."
- **(carry from M1)** A1–A5 Appendix amendments from job-0013 (research_mode field, event_type→intensity mapping, v0.2+ subtype payload typing, RunDocument.assessment note, A.6 cancel-error codes).
- **(carry from M1)** NFR-C-1 cost-line correction (~$170/mo M10 actual, not <$100).
- **(carry from M1)** NFR-P-1 first-token budget reality (3–8s warm vs 2s spec).
- **(carry from M1)** FR-AS-1 Gemini-3 substitution clause ("Gemini 3 when available, latest stable otherwise").
- **(carry from M1)** OQ-1 ratification: Cloud Run + WebSocket (`--use-http2 --session-affinity --min-instances=1`).
- **(carry from M1)** Gitignore identifier exposures — Lever A (sanitize) / B (rotate) / C (history purge).

**Carry-forward into sprint-05 (M3 — Web client skeleton):**

- M3 deliverables: React app with MapLibre displaying QGIS Server tiles, chat panel, layer toggle, pipeline strip components, WebSocket to agent.
- **Playwright + `SendUserFile` proactive loop** for AFK frontend iteration (user request 2026-06-05; memorialized in memory) — folds into the web specialist's M3 work. Closes job-0016 OQ-W-3 (Chromium provisioning) as a side effect.
- OQ-23E: NFR-P-3 tile-latency p50/p95 measurement once web client provides realistic measurement context.
- OQ-21A: monitor QGIS version drift (3.44 worker vs 3.40 grace2 env) — upgrade env if dev interop breaks.
- OQ-20G + OQ-23D: `notify_message_id` null pattern (subscribers correlate via outer `message.messageId`).
- Cloud Workflows definitions when M5 SFINCS solver opens (OQ-21F).
- Cosmetic scaling drift (OQ-F): auto-resolves on next service-touching apply.

**Sprint discipline notes (worth carrying into future sprints):**

- The **closeout workflow pattern** (recover-from-StructuredOutput-failure) is reusable; saw it work on jobs 0013, 0017, 0020. Consider promoting to a documented orchestrator recipe.
- **Scope additions mid-sprint** (job-0024) need explicit counter bump + manifest update + dependency-graph update; this sprint demonstrated the pattern cleanly.
- **Live substrate first, mocks only at one seam**: the testing discipline scaled from 23 M1 tests to 30 (7 new M2) without any new mocking; the worker round-trip + Pub/Sub envelope tests hit real cloud.
- **WMS URL contract change** (architectural pivot) propagated cleanly via 3 surgical edits (qgis-server.tf, job-0021 kickoff, job-0023 kickoff); zero rework on the worker code.
