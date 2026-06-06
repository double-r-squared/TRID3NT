# Audit: M2 acceptance: GetCapabilities + WMS GetMap + worker round-trip + M1 regression green

**Job ID:** job-0023-testing-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** testing
**Prerequisites:** job-0018-infra-20260605 (approved) — QGIS Server URL, GCS buckets, Pub/Sub topic, IAM policy expected shape. job-0019-engine-20260605 (approved) — sample `.qgs` uploaded to `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`, `styles/basemap.qml` baked into QGIS Server image. job-0020-engine-20260605 (approved) — `worker_round_trip` entrypoint + WorkerResult shape + Pub/Sub envelope shape + CLI args (`--qgs-uri`, `--layer-name`). job-0021-infra-20260605 (approved) — deployed Cloud Run Job `grace-2-pyqgis-worker` + IAM scoping + `make worker-run-job` target. job-0017-testing-20260605 (complete) — the 114-test M1 regression baseline (91 contracts + 23 acceptance) gated by `make test`.
**SRS references:** FR-QS-1/2/5/6 (QGIS Server + .qgs in GCS + preset + worker round-trip — all four are the M2 deliverable surface); FR-CE-1 (Cloud Run Job pattern verified); FR-MP-1/3 (canonical .qgs in GCS); NFR-R-4 (QGIS Server stateless — verify via image properties + min-instances=0); NFR-S-2 (service-account credentials); NFR-S-5 (no public buckets — assert PAP enforced); NFR-PO-3 (IaC integrity — `tofu plan` clean); NFR-P-3 (QGIS Server tile latency <1s p95 — measured-but-not-gated at M2, gated at M3); Invariants 2 (zero-LLM in worker — grep verified), 4 (Rendering through QGIS Server — verified live), 5 (Tier separation — bucket scoping verified), 6 (Metadata-payload pattern — bucket non-public + non-enumerable verified). All M1 SRS sections inherit (since the 114-test M1 regression is part of acceptance).

### Environment

Dev + prod substrate Linux (Debian 13 trixie, `Linux maturin 6.12.74+deb13+1-amd64`, x86_64). Container builds `linux/amd64`-only. Consume live cloud substrate from `PROJECT_STATE.md` + the upstream job audits — `make test-m2` runs against the deployed QGIS Server URL + GCS sample `.qgs` + deployed Cloud Run Job. Local Python uses `virtualenv` (NOT `python3-venv` — unavailable on Debian 13; sprint-03 pattern from jobs 0013/0015/0017). Existing M1 pytest harness at `tests/` from job-0017 stays as-is; `make test` (M1 regression) gates the no-regression criterion. `tests/m2/` is new for this job.

### Scope

1. **Create `tests/m2/` harness** — `live_qgis_server` + `live_worker` pytest markers parallel to the `live_gemini` + `live_atlas` markers from job-0017. New marker `live_qgis_server` requires the deployed Cloud Run service URL (from environment variable `GRACE2_QGIS_SERVER_URL`); `live_worker` requires `gcloud` ADC + project access. Document in `tests/m2/README.md`.
2. **Test (1) — QGIS Server `GetCapabilities` live transcript** — `tests/m2/test_qgis_server_capabilities.py`:
   - `curl -sf "<qgis-server-url>/ogc/?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"` returns HTTP 200; parseable XML; asserts `basemap-osm-conus` layer name present.
   - Capture verbatim HTTP transcript as artifact `tests/m2/artifacts/getcapabilities.xml`.
   - Marker: `@pytest.mark.live_qgis_server`. Local-fixture variant: parse a recorded GetCapabilities XML; mark `qualified (recorded, not live)` if the marker is skipped.
3. **Test (2) — Sample `.qgs` WMS GetMap render PNG** — `tests/m2/test_qgis_server_getmap.py`:
   - `GetMap` at 512x256 over CONUS bbox (matching job-0019 acceptance command), save PNG as `tests/m2/artifacts/sample-getmap.png`.
   - Assert PNG decodes (PIL/Pillow), file size > 1KB, pixel-variance check (`statistics.stdev` on grayscale conversion > 0 → non-blank).
   - The PNG is a SendUserFile-friendly artifact for the M2 close package.
   - Marker: `@pytest.mark.live_qgis_server`.
4. **Test (3) — PyQGIS worker round-trip transcript** — `tests/m2/test_pyqgis_worker_roundtrip.py`:
   - Stage: upload a fresh copy of `services/workers/pyqgis/sample_project/grace2-sample.qgs` to `gs://grace-2-hazard-prod-qgs/grace2-sample-test.qgs` (a test-scoped object — don't mutate the canonical one).
   - Execute: `gcloud run jobs execute grace-2-pyqgis-worker --region=us-central1 --args="--qgs-uri,gs://grace-2-hazard-prod-qgs/grace2-sample-test.qgs,--layer-name,acceptance-demo" --wait` (or via Python `google-cloud-run` client).
   - Capture: execution succeeded; logs show six FR-QS-6 steps; Pub/Sub message pulled (create temp subscription, pull, ack, delete sub).
   - Verify: post-execution `gcloud storage stat gs://grace-2-hazard-prod-qgs/grace2-sample-test.qgs` shows updated `md5Hash`; post-execution GetCapabilities on the same object shows 2 layers (`basemap-osm-conus` + `acceptance-demo`).
   - **Cleanup:** delete `gs://grace-2-hazard-prod-qgs/grace2-sample-test.qgs` + temp subscription at test teardown.
   - Save verbatim execution log as `tests/m2/artifacts/worker-roundtrip.log` and Pub/Sub envelope as `tests/m2/artifacts/worker-notify.json`.
   - Marker: `@pytest.mark.live_worker`. Local-fixture variant: run `worker_round_trip` from the `grace2` conda env against a local `.qgs` file (read/write local path instead of `/vsigs/`, stub Pub/Sub publisher) — gates the logic before live; mark live-skipped as `qualified (local-fixture variant ran)`.
5. **Test (4) — M1 regression suite (114 tests stay green)** — `tests/m2/test_m1_regression.py` (or extend Makefile target):
   - `make test` (the existing M1 target from job-0017) runs the 91 contracts + 23 acceptance tests; assert exit 0 + 114 passed.
   - Verifies no contract or acceptance regression from M2 work (gates the file-ownership boundaries that froze `packages/contracts/**`, `services/agent/**`, `web/**`).
   - Capture verbatim pytest transcript.
6. **Test (5) — Invariant verification** — `tests/m2/test_invariants_m2.py`:
   - Bucket PAP enforced: `gcloud storage buckets describe gs://grace-2-hazard-prod-qgs --format='value(iamConfiguration.publicAccessPrevention)'` == `enforced`; repeat for `-cog` and `-fgb` (Invariant 5, NFR-S-5).
   - No public IAM on buckets: `gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-qgs --format=json | jq` returns no `allUsers`/`allAuthenticatedUsers` binding.
   - Worker SA has only minimum-required roles: `gcloud projects get-iam-policy grace-2-hazard-prod --format=json | jq '.bindings[] | select(.members[]=="serviceAccount:grace-2-pyqgis-worker@...")'` shows only `storage.objectAdmin` (scoped to `-qgs` bucket conditional) and `pubsub.publisher` (scoped to topic conditional). NO `storage.admin`, NO project-wide grants (Invariant 6, NFR-S-2).
   - Worker code has zero LLM dependencies: `grep -rn -e 'gemini\|google.generativeai\|anthropic\|openai' services/workers/pyqgis/` returns ZERO matches (Invariant 2 mechanical guard).
   - QGIS Server is stateless: `gcloud run services describe grace-2-qgis-server --region=us-central1 --format='value(spec.template.metadata.annotations."autoscaling.knative.dev/minScale")'` == `0` (NFR-R-4 / Invariant 4 — substrate property check).
7. **Makefile target** — root `Makefile` additive: `make test-m2` runs `pytest tests/m2/ -v --tb=short` with both markers active (auto-skip the live markers if env vars unset, with a clear skip message). Document in `tests/m2/README.md`.
8. **Sprint-04 acceptance record** — `tests/m2/README.md` table mapping each of the 7 sprint exit criteria from `reports/sprints/sprint-04.md` to the test ID + artifact path that re-verified it (parallel to job-0017's M1 acceptance pattern).
9. **Headless gate + artifact evidence** — full test suite runs headless via `make test-m2` for CI; artifacts (`getcapabilities.xml`, `sample-getmap.png`, `worker-roundtrip.log`, `worker-notify.json`) are the headed-equivalent evidence for the closeout package. PNG artifact is SendUserFile-friendly.
10. **Open Questions to surface (TENTATIVE-tagged):**
    - Local-fixture variant for `test_pyqgis_worker_roundtrip.py`: requires the `grace2` conda env from job-0022 to run locally. TENTATIVE: gate it on the `grace2` env's presence via a conftest fixture; mark `qualified` if env absent (matches sprint-03 `live_gemini`/`live_atlas` pattern).
    - Whether to extend the M1 `make test` target to include `make test-m2` automatically (single regression gate) vs keeping them separate. TENTATIVE: keep separate at M2 — `make test` stays the M1 regression rerun; `make test-m2` is the M2 acceptance. Wire into a future `make test-all` if/when needed.
    - NFR-P-3 tile-latency measurement: measure but do not gate at M2 (no client consuming tiles yet). TENTATIVE: record p50/p95 for `GetMap` over 30 samples in the report as informational; gate begins at M3 when web client consumes WMS.
    - Perceptual-diff threshold for `sample-getmap.png`: pixel-variance > 0 is the M2 floor (catches blank/black tiles). TENTATIVE: leave at the floor for M2; perceptual-diff library + golden tile lands when first FR-QS-5 preset (flood depth) ships in M5.

### File ownership (exclusive)

- `tests/m2/` (entire directory; new)
- `tests/m2/test_qgis_server_capabilities.py`
- `tests/m2/test_qgis_server_getmap.py`
- `tests/m2/test_pyqgis_worker_roundtrip.py`
- `tests/m2/test_m1_regression.py`
- `tests/m2/test_invariants_m2.py`
- `tests/m2/fixtures/` (small fixtures only — the canonical sample `.qgs` lives under `services/workers/pyqgis/sample_project/` and is engine-owned)
- `tests/m2/artifacts/` (test-output artifacts; created at run time, gitignored or committed per the sprint-03 acceptance-table pattern)
- `tests/m2/README.md`
- `tests/conftest.py` (additive — register `live_qgis_server` + `live_worker` markers, parallel to existing `live_gemini`/`live_atlas`)
- Root `Makefile` (additive — `make test-m2` target only; do NOT modify `make test` or any other existing target)

**FROZEN (do NOT edit):** `tests/` outside `tests/m2/` (M1 harness from job-0017 is immutable — `make test` regression depends on its shape), `packages/contracts/**`, `services/agent/**`, `services/workers/**`, `web/**`, `styles/**`, `infra/**`, `docs/SRS_v0.3.md`, `public_hazard_catalog.yaml`.

### Cross-cutting principles in force
*Bundle small fixes; scan for all instances* — when this job touches a known class of issue (e.g., a missing label on a labeled resource), sweep the whole sprint scope for similar instances and surface in the report.

Cite by name from AGENTS.md § "Cross-cutting principles":
- **Pre-MVP scope — no legacy support.** No M0-tier branches, no Mac-paths, no AWS branches in fixtures or assertions.
- **Remove don't shim.** No `# TODO: when client consumes tiles` placeholders — write the NFR-P-3 measurement now, mark informational.
- **Live E2E validation required.** Every test runs against the deployed substrate (or its documented local-fixture variant); verbatim transcripts in the report; `tests/m2/artifacts/sample-getmap.png` attached to the closeout.
- **Diagnose before fix.** Per testing.md "every failure names the failing layer": each assertion's failure message identifies which layer broke (QGIS Server vs `.qgs` content vs `/vsigs/` GDAL config vs worker code vs Pub/Sub vs IAM vs Cloud Run Job).
- **Surface uncertainty.** Every contestable choice → Open Question with TENTATIVE tag.
- **Don't edit in-flight kickoffs.** Frozen.
- **Testing: test through real interfaces.** Real QGIS Server tiles, real Cloud Run Job execution, real GCS, real Pub/Sub. No stubs except at external boundaries (none in this job's scope — all M2 surfaces are internal/GCP).
- **Testing: cloud-dependent tests get a documented local-fixture variant — never silently skipped.** The worker round-trip's local variant runs in the `grace2` env against a local `.qgs`; mark `qualified` if the env is absent.
- **Testing: every failure names the failing layer.** Assertion messages must attribute (QGIS Server | `.qgs` | `/vsigs/` | worker code | Pub/Sub | IAM | Cloud Run Job).
- **Testing: output-format awareness.** Raster assertion: GetMap PNG (M2 is intermediate raster; first COG assertion lands at M5 when first SFINCS output ships).
- **Testing: performance numbers always carry environment context.** Any NFR-P-3 informational measurement reports region, sample size, time-of-day, QGIS Server image digest.

### Acceptance criteria (reviewer re-runs)

- `make test-m2` (from repo root, with `GRACE2_QGIS_SERVER_URL` set + gcloud ADC present) returns exit 0; all five tests pass (or local-fixture variants pass with `qualified` marker recorded).
- `make test` (M1 regression target from job-0017) returns exit 0; 114 tests pass (91 contracts + 23 acceptance), no regression.
- `tests/m2/artifacts/sample-getmap.png` exists, file size > 1KB, pixel-variance check passes (non-blank).
- `tests/m2/artifacts/getcapabilities.xml` parses; the `basemap-osm-conus` layer is named.
- `tests/m2/artifacts/worker-roundtrip.log` shows the six FR-QS-6 steps verbatim.
- `tests/m2/artifacts/worker-notify.json` shows the Pub/Sub completion envelope shape from job-0020.
- `tests/m2/README.md` contains the sprint-04 exit-criteria → test-ID → artifact table.
- Invariant verifications run live: PAP enforced on all three buckets, no public IAM, worker SA roles minimal, worker code has zero LLM-grep matches, QGIS Server min-instances=0.
- All Open Questions surfaced with TENTATIVE tags + SRS references.
- Sprint-04 acceptance verdict drafted in the report (per job-0017 pattern: criterion-by-criterion pass/qualified/fail with cited evidence).

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
