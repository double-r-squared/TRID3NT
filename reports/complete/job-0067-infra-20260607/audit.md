# Audit: IAM grant — pyqgis-worker SA reads runs bucket (OQ-62 follow-up)

**Job ID:** job-0067-infra-20260607, **Sprint:** sprint-09 (Stage B follow-up; gates live E2E for job-0062), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra

**Prerequisites:**
- job-0062 (APPROVED, commit f202a31) — surfaced OQ-62-WORKER-SA-RUNS-BUCKET-GRANT.
- job-0061 (APPROVED, commit 1b2f989) — established the IAM pattern + co-location convention in `infra/buckets.tf`.

**SRS references:** none beyond NFR-S-2 (already in force).

### Why this job exists

job-0062 lands the publish_layer atomic tool + PyQGIS worker raster path. The worker needs to read the flood-depth COG from `gs://grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif` to add it as a `QgsRasterLayer` against `/vsigs/grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif`. Without this grant, live E2E verification can't run (the worker errors at the GDAL VSI read).

### Scope

Mirror job-0061 exactly, but for the **pyqgis-worker** SA, on the **same runs bucket**:

1. `infra/buckets.tf` — add a single `google_storage_bucket_iam_member` co-located with the existing pyqgis-worker bindings (and right next to the `qgis_server_runs_viewer` grant from job-0061):
   ```hcl
   resource "google_storage_bucket_iam_member" "pyqgis_worker_runs_viewer" {
     bucket = google_storage_bucket.runs.name
     role   = "roles/storage.objectViewer"
     member = "serviceAccount:${google_service_account.pyqgis_worker.email}"
   }
   ```
   With a brief comment citing job-0062 + OQ-62-WORKER-SA-RUNS-BUCKET-GRANT.

2. Tofu plan should show 1-add 0-change 0-destroy (target the resource if the pre-existing Cloud Run scaling drift from OQ-61 still surfaces in untargeted plan; document the noise honestly).

3. Tofu apply.

4. Verify the grant lands via `gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-runs --format=json | grep -A 1 'pyqgis-worker'`. NO project-level grant (`gcloud projects get-iam-policy ... --filter=...pyqgis-worker`).

5. Live worker round-trip smoke (the real verification): trigger the PyQGIS worker Cloud Run Job with a publish-raster manifest pointing at one of the existing runs-bucket COGs (e.g., `gs://grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif` from job-0059). Worker should: download the .qgs, add the raster layer via `/vsigs/...`, write back, publish completion. If the worker still errors on the VSI read after the grant lands, document the actual error vs the expected GCS permission flow.

### File ownership (exclusive)
- `infra/buckets.tf` — single resource addition
- `reports/inflight/job-0067-infra-20260607/`

### FROZEN
- All other Tofu (`*.tf`) — same as job-0061
- All other paths

### Acceptance criteria
- [ ] `google_storage_bucket_iam_member.pyqgis_worker_runs_viewer` resource added
- [ ] Tofu plan shows the addition; apply clean
- [ ] `gcloud` IAM verification: SA is a bucket-scoped objectViewer; no project-level grant
- [ ] Live worker round-trip smoke succeeds (or honestly documents the next error)
- [ ] No edits to FROZEN paths
- [ ] Single commit
