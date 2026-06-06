# Report: M2 acceptance — GetCapabilities + WMS GetMap + worker round-trip + M1 regression green

**Job ID:** job-0023-testing-20260605
**Sprint:** sprint-04
**Specialist:** testing
**Task:** Stand up `tests/m2/` as the M2 acceptance suite (QGIS Server WMS, PyQGIS worker round-trip via Cloud Run Job, IaC integrity + bucket security posture); re-run the M1 regression; produce the sprint-04 exit-criteria acceptance record.
**Status:** ready-for-audit

## Summary

Authored seven live-substrate acceptance tests under `tests/m2/` covering all six sprint-04 exit criteria. Every test runs against the deployed cloud substrate — the QGIS Server Cloud Run service at `https://grace-2-qgis-server-pwvcfwv55q-uc.a.run.app` (job-0018/0024), GCS buckets `grace-2-hazard-prod-{qgs,cog,fgb}` (job-0018), Pub/Sub topic `grace-2-worker-events` (job-0018), and the PyQGIS worker Cloud Run Job `grace-2-pyqgis-worker` (job-0020 code + job-0021 container, image `sha256:fffd7e0f…`). All 7 M2 tests green; all 114 M1 regression tests (91 contracts + 23 acceptance) stay green; the 121-test combined `make test` run is clean.

## Changes Made

- **`tests/m2/__init__.py`** — empty package marker so pytest treats `tests/m2/` as an importable package and `from .conftest import …` lookups resolve.
- **`tests/m2/conftest.py`** — M2-specific pytest fixtures (additive over the M1 root `tests/conftest.py`):
  - `qgis_server_url` (session) — env-var-overridable (`GRACE2_QGIS_SERVER_URL`), defaults to the deployed Cloud Run URL captured at the time of this job.
  - `sample_qgs_uri` (session) — defaults to `/mnt/qgs/grace2-sample.qgs` per the job-0024 WMS URL contract; env-var-overridable.
  - `qgs_bucket` / `gcp_project` / `gcp_region` / `pubsub_topic` / `worker_job_name` (session) — env-var-overridable factual handles to the substrate.
  - `gcloud_bin` / `adc_available` (session) — resolves `gcloud` on PATH or at `~/tools/google-cloud-sdk/bin/gcloud` (the canonical dev-box install path per `PROJECT_STATE.md` § Environment facts); ADC check uses `gcloud auth application-default print-access-token`.
  - `repo_root_m2` / `artifacts_dir` (session) — small ergonomic handles.
  - `pytest_configure` registers three M2 markers (`live_qgis_server`, `live_worker`, `live_tofu`) parallel to the existing M1 `live_gemini`/`live_atlas` markers.
- **`tests/m2/test_qgis_server_wms.py`** — two tests:
  - `test_getcapabilities_returns_valid_xml` — `urllib.request` GET against the deployed QGIS Server with `MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities`; asserts HTTP 200, body parses as XML, root element is `WMS_Capabilities`, and `<Name>basemap-osm-conus</Name>` is present. Writes verbatim XML to `tests/m2/artifacts/getcapabilities.xml`.
  - `test_getmap_returns_png` — GET against `…REQUEST=GetMap&LAYERS=basemap-osm-conus&BBOX=24,-125,50,-66&CRS=EPSG:4326&WIDTH=800&HEIGHT=400&FORMAT=image/png&STYLES=`; asserts HTTP 200, magic bytes `89 50 4E 47 0D 0A 1A 0A`, body > 1KB, and that the body isn't an XML `<ServerException>` disguised as 200. Writes the PNG to `tests/m2/artifacts/sample-getmap.png` (artifact for the closeout package).
- **`tests/m2/test_pyqgis_worker_roundtrip.py`** — three tests sharing one module-scoped `worker_run_result` fixture that runs a single live Cloud Run Job execution end-to-end:
  1. Copies `services/workers/pyqgis/sample_project/grace2-sample.qgs` to a unique `gs://grace-2-hazard-prod-qgs/acceptance-<random>.qgs` (test-scoped, never mutates the canonical object).
  2. Creates a temp Pub/Sub subscription on `grace-2-worker-events` so the completion envelope is captured the moment the worker publishes.
  3. Invokes `gcloud run jobs execute grace-2-pyqgis-worker --args=--qgs-uri,/mnt/qgs/<test-input>,--layer-to-add,acceptance-test-layer-<random> --wait`.
  4. Pulls the published envelope from the temp subscription with bounded retries (~60s budget, 3s poll).
  5. Downloads the mutated `.qgs` back from GCS for layer-count assertion.
  6. Cleans up: deletes the temp subscription and the test-scoped GCS object at teardown.
  - `test_worker_job_execute_succeeds` — `gcloud run jobs execute` exits 0; the latest execution's `status.conditions` show `Completed=True`.
  - `test_worker_mutation_visible_in_gcs` — the downloaded `.qgs` has exactly 2 `<maplayer>/<layername>` entries: `basemap-osm-conus` (canonical) plus `acceptance-test-layer-<random>` (appended).
  - `test_worker_publishes_envelope` — the captured envelope shape: `qgs_uri == /mnt/qgs/<test-input>`, `status == "ok"`, `layers_after` contains both layers, `qgs_version` is a non-empty string, `ts` is a Z-suffixed UTC ISO-8601 string, `notify_message_id` key is present (always null in the published payload per the OQ-20G documented behaviour).
- **`tests/m2/test_iac_clean.py`** — two tests:
  - `test_no_public_buckets` — for each of `grace-2-hazard-prod-{qgs,cog,fgb}`: `gcloud storage buckets describe --format=json` confirms `public_access_prevention == "enforced"` and `uniform_bucket_level_access` is enabled; `gcloud storage buckets get-iam-policy` confirms no `allUsers` or `allAuthenticatedUsers` member in any binding. (NFR-S-5, Invariant 5.)
  - `test_tofu_plan_clean` — runs `tofu -chdir=infra plan -no-color -target=…` against the 10 M2-owned resources (skipping the Atlas resources that require the ad-hoc API-key ritual per `infra/README.md`); asserts exit 0 + (stdout contains "No changes" OR contains the documented OQ-F cosmetic scaling-block normalization drift on `google_cloud_run_v2_service.qgis_server`).
- **`tests/m2/artifacts/`** — auto-created at test run; carries the live transcripts: `getcapabilities.xml`, `sample-getmap.png` (332 KB), `worker-notify-<rand>.json`, `mutated-<rand>.qgs`, `execute-stdout-<rand>.json`, `execute-stderr-<rand>.log`. Per the file-ownership boundary this directory is testing-owned and is included in the M2 commit so the audit can re-inspect the live evidence.
- **`Makefile`** (root, additive only):
  - Added `test-m2` to `.PHONY`.
  - Added a help line for `test-m2`.
  - Added the `test-m2` target — runs `$(TEST_VENV)/bin/python -m pytest tests/m2 -v --tb=short` (reuses the `.venv-agent` venv already established by job-0017, no extra bootstrap).
  - `make test` (the M1 regression target) is **not modified** — it still runs the same `pytest tests` command and now collects the 7 M2 tests as additional acceptance tests on top of the 91+23=114 M1 baseline; the M2 markers auto-skip if their substrate is unreachable so the M1 portion remains the M1 portion under any env constraint.

## Decisions Made

- **Decision:** Test through real cloud substrate end-to-end, including a live Cloud Run Job invocation per test session.
  - Rationale: testing.md "test through real interfaces" — a mocked worker does not test the worker; a stubbed QGIS Server does not test the server. The kickoff demanded live evidence (PNG artifact, Pub/Sub envelope, execution log) for the M2 acceptance record.
  - Alternatives considered: a local-fixture variant in the `grace2` conda env. Rejected as the *primary* path — local fixture would short-circuit the substrate; kept as documented escape via `GRACE2_SKIP_LIVE_WORKER=1` for offline dev.

- **Decision:** Share one Cloud Run Job execution across the three worker tests via a module-scoped fixture (`worker_run_result`).
  - Rationale: each live execution costs ~60-80s wall-clock (gcsfuse init + PyQGIS app startup + mutation + GCS upload + Pub/Sub publish + `--wait` polling). Running three independent executions would triple the suite latency and Pub/Sub message volume with no acceptance benefit — the three assertions are facets of the same execution, not independent behaviours.
  - Alternatives considered: per-test execution. Rejected on cost grounds (~3 minutes vs ~80 seconds).

- **Decision:** Parse the mutated `.qgs` via stdlib `xml.etree.ElementTree` rather than PyQGIS.
  - Rationale: the M1 venv (`.venv-agent`) does not have PyQGIS installed; importing PyQGIS only works inside the `grace2` conda env. Forcing the M2 suite to require the conda env adds friction with no acceptance benefit — the `.qgs` is a stable XML schema with `<maplayer>/<layername>` elements that `ET.iter` parses identically to QGIS' own parser.
  - Alternatives considered: invoke a small PyQGIS helper subprocess (`conda run -n grace2 python -c "…"`). Rejected as over-engineering for a layer-count check.

- **Decision:** Target the M2 resource set explicitly in `test_tofu_plan_clean` rather than running a full `tofu plan`.
  - Rationale: a full plan requires `MONGODB_ATLAS_PUBLIC_KEY` + `MONGODB_ATLAS_PRIVATE_KEY` to authenticate the `mongodbatlas` provider; per `infra/README.md` the canonical workflow mints those keys ad-hoc for `tofu apply` and **revokes them after**. A passive M2 re-verification suite has no such keys — running a full plan crashes with HTTP 401 against Atlas. Targeting the 10 M2 resources keeps the test honest about what M2 owns.
  - Alternatives considered: require the keys at test time, marking `qualified` when absent. Rejected: marks the test perpetually qualified in CI for a layer M2 does not own.

- **Decision:** Carry the deployed QGIS Server URL as the env-var-default fixture value rather than reading from a config file.
  - Rationale: every other path consults `PROJECT_STATE.md` for substrate facts, but `tests/conftest.py` already follows the "env-var override with sensible default" pattern (cf. `atlas_srv`). Matches the precedent and keeps the test self-contained.
  - Alternatives considered: read from `infra/.terraform.tfstate` via the `tofu output` indirection. Rejected as fragile (requires tofu init + Atlas keys for the larger state file).

- **Decision:** Register the M2 markers in `tests/m2/conftest.py` via `pytest_configure`, not by editing `tests/pyproject.toml`.
  - Rationale: the kickoff's scope notes "tests/conftest.py (additive — register live_qgis_server + live_worker markers)" — I chose the lower-scope local conftest because `tests/pyproject.toml` is M1's contract surface (job-0017) and `pytest_configure(config)` in the m2 conftest is recognized by pytest discovery at collection time. Avoids touching the M1 file at all.
  - Alternatives considered: append the markers to `tests/pyproject.toml [tool.pytest.ini_options].markers`. Rejected on minimal-touch grounds.

- **Decision:** `make test` (M1 target) continues to invoke `pytest tests`, which now collects the 7 M2 tests too.
  - Rationale: the kickoff scope explicitly says "do NOT touch the M1 test targets". The M2 tests are gated by markers that auto-skip when their substrate is unreachable, so adding them to the `make test` collection cannot regress the M1 portion. The combined run remains "M1 regression green + M2 acceptance green" which is what EC7 demands.
  - Alternatives considered: scope `make test` to `--ignore=tests/m2`. Rejected as a modification to the M1 target text, which the kickoff explicitly forbade.

## Invariants Touched

- **Invariant 2 (Deterministic workflows):** preserves — the M2 acceptance suite has zero LLM calls. The worker code (which this suite tests) was already verified LLM-free by the job-0020 grep; this suite asserts the same property indirectly by exercising the same code path through Cloud Run Jobs and observing the structured envelope (no narrative output, no model dispatch in the call chain).
- **Invariant 4 (Rendering through QGIS Server):** preserves — every Tier B render in this suite goes through the deployed QGIS Server WMS endpoint (`GetCapabilities` + `GetMap`); no client-side or test-harness rendering is performed. The PyQGIS worker round-trip is the only `.qgs`-writer code path exercised, matching the architectural asymmetry job-0021 enforced via the `read_only=true` mount on QGIS Server vs `read_only=false` on the worker.
- **Invariant 5 (Tier separation):** verifies live — `test_no_public_buckets` asserts `public_access_prevention=enforced` and UBLA on all three M2 buckets, plus the absence of `allUsers`/`allAuthenticatedUsers` IAM bindings. No client-direct GCS read path is exercised anywhere in the suite (PNG comes from QGIS Server WMS, not bucket reads).
- **Invariant 6 (Metadata-payload pattern):** preserves — the worker writes a `.qgs` payload to GCS and publishes a structured metadata envelope to Pub/Sub. The suite captures both halves of the pattern and asserts they match (`layers_after` in the published envelope equals the layer set in the downloaded `.qgs`).
- **Invariant 9 (No cost theater):** preserves — no `cost` / `usd` / `cents` fields anywhere in the M2 suite's assertions or artifact shapes.

## Open Questions

- **OQ-23A — `make test` now collects M2 tests in addition to M1.** The kickoff scope said "additive — do NOT touch tests/protocol/ or tests/integration/ from M1" and "additive only — do not touch the M1 test targets". I chose to leave `make test` invoking `pytest tests`, which now naturally collects the new M2 tests. The kickoff is ambiguous between "leave `make test` shape exactly 114-collected" and "leave the M1 test code shape unchanged, M1 still 114 green". The 121-test combined run is a strict superset of the 114-test M1 run and EC6 is satisfied (M1 regression unchanged + M2 acceptance green). **TENTATIVE:** the chosen interpretation is correct — restate as a no-op decision if not. SRS ref: kickoff EC6 + AGENTS.md "Live E2E validation required" (`make test` running M2 too is just more live evidence).

- **OQ-23B — `tofu plan` test targets the M2 resource set rather than running a full plan.** The Atlas API key ritual makes a full plan unreachable from a passive acceptance suite; the OQ-F cosmetic drift is a known carry-forward from job-0018. **TENTATIVE:** the targeted-plan approach matches the kickoff's explicit allowance for "exit code 0 and stdout contains 'No changes' OR only contains the documented OQ-F cosmetic scaling drift carry-forward." Future infra-touching jobs should periodically run a full plan with minted Atlas keys (the documented "least-privilege ritual" in `infra/README.md`) to confirm the Atlas-side resources have not drifted. SRS ref: NFR-PO-3.

- **OQ-23C — Worker QGIS version (3.44.11-Solothurn) vs local `grace2` env (3.40.3-Bratislava).** Captured live in the published envelope: `qgs_version: "3.44.11-Solothurn"` (worker container, baked from the same `qgis/qgis-server` digest as the QGIS Server image — job-0021 OQ-21A). The local conda env (job-0022) is still pinned at 3.40.3-Bratislava. The mutated `.qgs` still parses cleanly via `xml.etree` (used by this suite) and via `QgsProject.read()` in the worker, so no regression observed yet. **TENTATIVE:** carry-forward of job-0021 OQ-21A; not blocking M2 acceptance. SRS ref: FR-QS-6.

- **OQ-23D — `notify_message_id: null` in the published envelope is documented behavior, not a bug.** The worker constructs the envelope *before* `publisher.publish().result()` returns the message id (chicken-and-egg per the job-0020 OQ-20G). The M2 suite asserts the key is present but does not assert non-null. Subscribers should use the outer Pub/Sub `message.messageId` for correlation. **TENTATIVE:** carry-forward of job-0020 OQ-20G; no change. SRS ref: FR-QS-6 step 5.

- **OQ-23E — NFR-P-3 tile latency measurement.** The kickoff floats this as TENTATIVE-deferred ("measure but do not gate at M2"). I did not add the 30-sample p50/p95 measurement in this job because (a) the deployed Cloud Run instance is scale-to-zero (cold-start dominates), making the number meaningless without warm-vs-cold separation; (b) the first real consumer is the web client at M3, which is the only realistic measurement context per the kickoff. **TENTATIVE:** defer to M3 when the web client lands. SRS ref: NFR-P-3.

## Dependencies and Impacts

- **Depends on:** job-0018 (QGIS Server URL + buckets + Pub/Sub), job-0019 (sample `.qgs` + basemap.qml), job-0020 (worker code), job-0021 (worker container + Cloud Run Job + worker SA), job-0022 (grace2 conda env — used for env verification only, not for the M2 suite which runs in `.venv-agent`), job-0024 (WMS URL contract `/mnt/qgs/`).
- **Affects:**
  - **Future agent integration (M3/M4):** the agent's `tools/run_pyqgis_worker_round_trip` atomic tool will land then; the M2 worker-roundtrip test pattern (temp subscription + Cloud Run Job execute + envelope assertion) is reusable for that integration test.
  - **Future web-tile consumption (M3):** the `test_getmap_returns_png` test is the substrate for the NFR-P-3 p95 measurement that gates at M3.
  - **Future SFINCS solver acceptance (M5):** the `test_pyqgis_worker_roundtrip` pattern (Cloud Run Job execute + Pub/Sub envelope capture + GCS round-trip) is the template for solver-run acceptance tests; the cancellation chain (Invariant 8) will extend it.

## Verification

### Environment

```
=== uname ===
Linux maturin 6.12.74+deb13+1-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.74-2 (2026-03-08) x86_64 GNU/Linux

=== python (grace2 conda env) ===
Python 3.12.13
QGIS_VERSION = 3.40.3-Bratislava

=== python (M1 .venv-agent venv used by pytest) ===
Python 3.13.5

=== gcloud ===
Google Cloud SDK 571.0.0
=== gcloud project ===
[core]
project = grace-2-hazard-prod
=== gcloud account ===
*       natealmanza3@gmail.com
```

### Live commands run

- `curl -sf "https://grace-2-qgis-server-pwvcfwv55q-uc.a.run.app/ogc/?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"` → HTTP 200, valid `<WMS_Capabilities>` XML, contains `<Name>basemap-osm-conus</Name>`. Captured in `tests/m2/artifacts/getcapabilities.xml`.
- `curl -sf "<url>?MAP=/mnt/qgs/grace2-sample.qgs&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=basemap-osm-conus&CRS=EPSG:4326&BBOX=24,-125,50,-66&WIDTH=800&HEIGHT=400&FORMAT=image/png&STYLES="` → HTTP 200, PNG image data, 800×400, 8-bit/color RGBA, 332 KB. Captured in `tests/m2/artifacts/sample-getmap.png`.
- `gcloud run jobs execute grace-2-pyqgis-worker --args=--qgs-uri,/mnt/qgs/acceptance-49c6c439.qgs,--layer-to-add,acceptance-test-layer-49c6c439 --wait` → exit 0; execution `Completed=True`. stdout JSON captured in `tests/m2/artifacts/execute-stdout-49c6c439.json`.
- `gcloud pubsub subscriptions pull <temp-sub> --auto-ack --format=json` → captured envelope at `tests/m2/artifacts/worker-notify-49c6c439.json`:

  ```json
  {
    "qgs_uri": "/mnt/qgs/acceptance-49c6c439.qgs",
    "layers_before": ["basemap-osm-conus"],
    "layers_after": ["acceptance-test-layer-49c6c439", "basemap-osm-conus"],
    "notify_message_id": null,
    "status": "ok",
    "error": null,
    "qgs_version": "3.44.11-Solothurn",
    "ts": "2026-06-06T07:40:18.171Z"
  }
  ```
- `gcloud storage cp gs://grace-2-hazard-prod-qgs/acceptance-49c6c439.qgs <local>` → downloaded; XML parse showed 2 `<maplayer>/<layername>` entries: `basemap-osm-conus` + `acceptance-test-layer-49c6c439`.
- `gcloud storage buckets describe gs://grace-2-hazard-prod-{qgs,cog,fgb}` → all show `public_access_prevention=enforced` and `uniform_bucket_level_access=True`; no public IAM members on any binding.
- `tofu -chdir=infra plan -no-color -target=<10 M2 resources>` → exit 0; output: `Plan: 0 to add, 1 to change, 0 to destroy.` with the diff being the documented OQ-F cosmetic scaling-block normalization on `google_cloud_run_v2_service.qgis_server`.

### Test runs

`make test-m2` (M2 acceptance suite):

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.0.3, pluggy-1.6.0
collected 7 items

tests/m2/test_iac_clean.py::test_no_public_buckets PASSED                [ 14%]
tests/m2/test_iac_clean.py::test_tofu_plan_clean PASSED                  [ 28%]
tests/m2/test_pyqgis_worker_roundtrip.py::test_worker_job_execute_succeeds PASSED [ 42%]
tests/m2/test_pyqgis_worker_roundtrip.py::test_worker_mutation_visible_in_gcs PASSED [ 57%]
tests/m2/test_pyqgis_worker_roundtrip.py::test_worker_publishes_envelope PASSED [ 71%]
tests/m2/test_qgis_server_wms.py::test_getcapabilities_returns_valid_xml PASSED [ 85%]
tests/m2/test_qgis_server_wms.py::test_getmap_returns_png PASSED         [100%]

======================== 7 passed in 139.44s (0:02:19) =========================
```

`make test` (M1 regression — 91 contracts + 23 acceptance + 7 M2 collected; 1 live_gemini deselected):

```
==> packages/contracts/tests (unit suite)
91 passed in 0.25s
==> tests/ (M1 acceptance suite — protocol conformance + negative controls + integration)
================= 30 passed, 1 deselected in 247.01s (0:04:07) =================
```

Combined: **121 / 121 green** (91 contracts + 23 M1 acceptance + 7 M2 acceptance), 1 deselected (live_gemini).

### Sprint-04 exit-criteria acceptance record

| # | Criterion | Status | Evidence |
|---|---|---|---|
| EC1 | QGIS Server Cloud Run service answers `GetCapabilities` against `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`; image digest-pinned; `--min-instances=0`; stateless | **pass** | `tests/m2/test_qgis_server_wms.py::test_getcapabilities_returns_valid_xml` (live HTTP 200 + valid `<WMS_Capabilities>` XML naming `basemap-osm-conus`); artifact `tests/m2/artifacts/getcapabilities.xml`; substrate from job-0018 + job-0024 audits (image `@sha256:a703476…`, scale-to-zero). |
| EC2 | Three GCS buckets PAP+UBLA+SA-scoped; Pub/Sub topic `grace-2-worker-events` exists; tofu plan clean | **pass** | `tests/m2/test_iac_clean.py::test_no_public_buckets` (live PAP=enforced + UBLA=true + zero `allUsers` IAM on all three buckets); `tests/m2/test_iac_clean.py::test_tofu_plan_clean` (`exit=0`, "No changes" allowance OR the OQ-F cosmetic scaling drift, as job-0018 audit documented). |
| EC3 | Sample `.qgs` round-trips cleanly; uploaded to GCS; `styles/basemap.qml` matches layer name; QGIS Server renders a non-blank WMS tile | **pass** | `tests/m2/test_qgis_server_wms.py::test_getmap_returns_png` (live PNG returned: magic bytes `89 50 4E 47`, 800×400, 332 KB, non-blank); artifact `tests/m2/artifacts/sample-getmap.png`. Substrate from job-0019 + job-0024. |
| EC4 | PyQGIS worker `worker_round_trip` runs end-to-end (read → mutate → write → notify); zero LLM in body; mutation visible in GCS; envelope captured | **pass** | `tests/m2/test_pyqgis_worker_roundtrip.py::test_worker_job_execute_succeeds` (Cloud Run Job exit 0 + `Completed=True`); `test_worker_mutation_visible_in_gcs` (downloaded `.qgs` has 2 layers); `test_worker_publishes_envelope` (envelope shape matches: `qgs_uri`, `status=ok`, `layers_after`, `qgs_version`, `ts`). Artifacts: `worker-notify-49c6c439.json`, `mutated-49c6c439.qgs`, `execute-stdout-49c6c439.json`. Substrate: job-0020 code + job-0021 container `sha256:fffd7e0f…`. Invariant 2 LLM-free guarantee carried from job-0020 grep. |
| EC5 | grace2 conda env on Debian via conda-forge — QGIS 3.40.3, dead deps stripped, `python -c "from qgis.core import QgsProject"` succeeds | **pass** | Env verification in this job's environment block: `conda activate grace2 && python -c "from qgis.core import Qgis; print(Qgis.QGIS_VERSION)"` → `3.40.3-Bratislava`; substrate from job-0022. |
| EC6 | `make test` green: 91 contracts + 23 acceptance = 114 M1 tests pass unchanged; `make test-m2` green | **pass** | `make test`: 91 contracts passed; 30 acceptance/protocol/integration/m2 collected, all 30 passed (1 live_gemini deselected) — the 23 M1 acceptance tests remain unchanged + 7 M2 added. `make test-m2`: 7/7 passed. Transcripts above. |

The kickoff-numbered exit criteria are 6 in the kickoff prose but the sprint-04 manifest lists 7 (with `make test-m2` carved out of EC6 as a separate EC7). The table above presents both shapes consistently (EC1–EC5 are unambiguous; EC6 here folds the manifest's EC6 + EC7 since `make test` and `make test-m2` are both green in the same run).

### Results

- **pass.** All seven M2 acceptance tests run live against the deployed substrate; all assertions hold; all 114 M1 regression tests still pass; all six sprint-04 exit criteria verified with cited evidence.

