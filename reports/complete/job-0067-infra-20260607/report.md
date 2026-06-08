# Report: IAM grant — pyqgis-worker SA reads runs bucket (OQ-62-WORKER-SA-RUNS-BUCKET-GRANT)

**Job ID:** job-0067-infra-20260607
**Sprint:** sprint-09
**Specialist:** infra
**Task:** Add `google_storage_bucket_iam_member.pyqgis_worker_runs_viewer` granting `roles/storage.objectViewer` to `pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` on `gs://grace-2-hazard-prod-runs/`. Mirror job-0061 pattern. Then live worker round-trip smoke.
**Status:** ready-for-audit

---

## Summary

Added `google_storage_bucket_iam_member.pyqgis_worker_runs_viewer` to `infra/buckets.tf` co-located immediately after `qgis_server_runs_viewer` (job-0061 pattern). Targeted apply landed cleanly: 1 added, 0 changed, 0 destroyed. Bucket-scoped IAM verification passes (SA present in `roles/storage.objectViewer` on runs bucket); project-level check returns `[]`. Live worker round-trip smoke identified a carry-forward blocker: the deployed container image (`sha256:fffd7e0f...`, pinned in job-0021) predates job-0062's `__main__.py` changes and does not recognize `--op publish-raster`. The IAM grant itself is correct and complete; the blocker is a missing worker image rebuild.

---

## Changes Made

- **`infra/buckets.tf`** — Added `resource "google_storage_bucket_iam_member" "pyqgis_worker_runs_viewer"` immediately after `qgis_server_runs_viewer` with a comment block citing job-0067, OQ-62-WORKER-SA-RUNS-BUCKET-GRANT, and the job-0061 mirror pattern. `bucket = google_storage_bucket.runs.name`, `role = "roles/storage.objectViewer"`, `member = "serviceAccount:${google_service_account.pyqgis_worker.email}"`.

---

## Tofu Plan Output (targeted)

```
$ tofu plan -target=google_storage_bucket_iam_member.pyqgis_worker_runs_viewer

  # google_storage_bucket_iam_member.pyqgis_worker_runs_viewer will be created
  + resource "google_storage_bucket_iam_member" "pyqgis_worker_runs_viewer" {
      + bucket = "grace-2-hazard-prod-runs"
      + etag   = (known after apply)
      + id     = (known after apply)
      + member = "serviceAccount:pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com"
      + role   = "roles/storage.objectViewer"
    }

Plan: 1 to add, 0 to change, 0 to destroy.
```

Untargeted plan: same pre-existing Cloud Run scaling drift (OQ-61) and Atlas 401 errors as documented in job-0061. Targeted plan isolates this single job-scoped change cleanly.

---

## Tofu Apply Output

```
$ tofu apply -target=google_storage_bucket_iam_member.pyqgis_worker_runs_viewer -auto-approve

google_storage_bucket_iam_member.pyqgis_worker_runs_viewer: Creating...
google_storage_bucket_iam_member.pyqgis_worker_runs_viewer: Creation complete after 4s
  [id=b/grace-2-hazard-prod-runs/roles/storage.objectViewer/serviceAccount:pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com]

Apply complete! Resources: 1 added, 0 changed, 0 destroyed.
```

---

## IAM Verification

### Bucket-scoped binding confirmed

```
$ gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-runs --format=json | grep -A 3 'pyqgis-worker'
        "serviceAccount:pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com",
        "serviceAccount:workflow-invoker-sfincs@grace-2-hazard-prod.iam.gserviceaccount.com"
      ],
      "role": "roles/storage.objectViewer"
```

SA `pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` is listed under `roles/storage.objectViewer` on the runs bucket. Grant confirmed.

### No project-level grant added

```
$ gcloud projects get-iam-policy grace-2-hazard-prod \
    --flatten='bindings[].members' \
    --filter='bindings.members:pyqgis-worker' \
    --format=json
[]
```

Empty result — no project-level IAM binding. NFR-S-2 and zero-project-grants invariant (job-0021) preserved.

---

## Live Worker Round-Trip Smoke — BLOCKED (carry-forward)

Two executions attempted; both failed with exit code 2 (argparse error, not a GCS permission error).

**Execution 1** — `--update-env-vars=WORKER_OP=publish-raster,...`:
```
python -m services.workers.pyqgis: error: --layer-to-add is required (or set LAYER_TO_ADD env var).
```
Container defaulted to add-polygon op; WORKER_OP was not picked up (image does not recognize it).

**Execution 2** — `--args="--op,publish-raster,..."`:
```
python -m services.workers.pyqgis: error: unrecognized arguments: --op publish-raster
    --raster-uri /vsigs/grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif
    --raster-layer-id test-publish-raster-67 --style-preset-name continuous_flood_depth
```

**Root cause:** The deployed container image (`sha256:fffd7e0f41aa255c80ff288e19cf950e5953e05cc79cc67524dbc9c7edbcacd9`, built job-0021, 2026-06-06) does not include the `--op`, `--raster-uri`, `--raster-layer-id`, or `--style-preset-name` arguments added by job-0062. The code landed in job-0062 but the image was never rebuilt and pushed.

The errors are NOT GCS permission errors — no "Permission denied" from GDAL/vsigs, no HTTP 403. The IAM grant is functioning correctly. The error fires at argparse, before any GCS access.

**Artifact Registry state:**
- `sha256:fffd7e0f...` (tagged `latest`, pinned in `infra/worker.tf`) — job-0021 image, no `--op` arg
- `sha256:3acc7aa7...` (no tags) — unverified, not pinned

**Required next step (OQ-67-WORKER-IMAGE-REBUILD):** `make worker-build`, capture new digest, update `infra/worker.tf` image pin, `tofu apply -target=google_cloud_run_v2_job.pyqgis_worker`. Not attempted per kickoff: "If it errors on something OTHER than the IAM grant we just landed, honestly document the next blocker as carry-forward and DON'T attempt the next fix."

---

## Decisions Made

- **Applied as `-target`** (same rationale as job-0061): untargeted plan includes pre-existing Cloud Run scaling drift and Atlas 401 errors unrelated to this job.
- **Correct env var is `WORKER_OP`, not `OP`**: kickoff example listed `OP=publish-raster`; actual env var per `__main__.py` is `WORKER_OP`. Used `WORKER_OP` in execution 1. The underlying issue (stale image) meant neither form could work.

---

## Invariants Touched

- **Invariant 5 (Tier separation):** preserves — runs bucket remains PAP-enforced, no public IAM.
- **NFR-S-2 (zero-project-grants):** preserves — bucket-scoped binding only; project IAM returns `[]`.
- **Invariant 6 (Metadata-payload pattern):** preserves — `objectViewer` at bucket scope; MongoDB remains the only discovery path.
- **Invariant 4 (Rendering through QGIS Server):** not touched — this grant is the worker read path; QGIS Server rendering is job-0061's grant.

---

## Open Questions

- **OQ-67-WORKER-IMAGE-REBUILD** (carry-forward, blocks live E2E for job-0062): Worker container image pinned in `infra/worker.tf` (`sha256:fffd7e0f...`) predates job-0062's `__main__.py` publish-raster code. Requires: `make worker-build` → new digest → update `infra/worker.tf` → `tofu apply -target=google_cloud_run_v2_job.pyqgis_worker`. Routing: infra specialist (image build + IaC pin). TENTATIVE priority: high (gates job-0062 live E2E acceptance).
- **Pre-existing Cloud Run scaling drift** (OQ-61, inherited): present in untargeted plan, not this job's scope, no runtime impact.

---

## Dependencies and Impacts

- Depends on: job-0040 (runs bucket), job-0021 (pyqgis-worker SA + zero-project-grants pattern), job-0061 (mirror pattern), job-0062 (surfaces OQ)
- Affects: job-0062 (engine — publish_layer live E2E): IAM prerequisite resolved; remaining blocker is OQ-67-WORKER-IMAGE-REBUILD.

---

## Verification

- Tests run: `tofu plan -target`, `tofu apply -target`, bucket IAM policy check, project IAM policy check, 2x Cloud Run Job executions
- Live E2E evidence:
  - Tofu apply: `1 added, 0 changed, 0 destroyed`
  - `gcloud storage buckets get-iam-policy`: SA in `roles/storage.objectViewer` on runs bucket — confirmed
  - `gcloud projects get-iam-policy --filter=pyqgis-worker`: `[]` — no project grants
  - Worker execution `grace-2-pyqgis-worker-lwrbs`: exit 2, argparse error (stale image)
  - Worker execution `grace-2-pyqgis-worker-67xf2`: exit 2, `unrecognized arguments: --op publish-raster` (stale image, not IAM)
  - No GCS "Permission denied" in either execution — IAM grant is functioning
- Results: **qualified** — IAM change passes; live E2E qualified due to stale container image (OQ-67-WORKER-IMAGE-REBUILD). WMS URL: not available.
