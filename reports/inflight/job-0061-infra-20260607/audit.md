# Audit: IAM grant — QGIS Server runtime SA reads runs bucket

**Job ID:** job-0061-infra-20260607, **Sprint:** sprint-09, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra

**Prerequisites:**
- `docs/decisions/layer-emission-contract.md` (ADOPTED 2026-06-07)
- job-0040 (infra, APPROVED): runs bucket `gs://grace-2-hazard-prod-runs/` + SFINCS solver container + IAM mirror pattern.
- job-0029 (infra, APPROVED): QGIS Server CORS + canonical `/mnt/qgs/` mount.
- `infra/qgis-server.tf` (lines 72-82, 161-167): QGIS Server runtime SA + bucket grants on `-qgs`/`-cog`/`-fgb`.

**SRS references** (narrow file loading only):
- `docs/srs/04-non-functional-requirements.md` NFR-S-2 (service-account-scoped IAM, no project-level grants)
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Why this job exists

QGIS Server's runtime SA currently has `roles/storage.objectViewer` on `-qgs`/`-cog`/`-fgb` (per `qgis-server.tf` / `infra/qgis-server/buckets.tf`). It does NOT have read on `gs://grace-2-hazard-prod-runs/`. When the sprint-09 `publish_layer` tool (job-0062) registers a flood-depth COG from the runs bucket into the `.qgs` via `/vsigs/grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif`, the WMS render request will fail with a GDAL "Permission denied" error because the runtime SA can't read the COG.

### Scope

1. **`infra/qgis-server.tf`** (OR `infra/qgis-server/buckets.tf` — wherever the existing -qgs/-cog/-fgb grants live; co-locate with them): add a `google_storage_bucket_iam_member` resource granting `roles/storage.objectViewer` on `gs://grace-2-hazard-prod-runs/` to the `grace-2-qgis-server` service account.

2. **Tofu plan + apply** locally. Verify the grant lands via `gcloud projects get-iam-policy` filtered by SA. Bucket-scoped only — no project-level grant (per NFR-S-2 + the Invariant 5 zero-project-grants pattern established in job-0021).

3. **Live WMS verification (smoke):** once the grant lands, manually drop a small test `.qgs` referencing a runs-bucket COG into the -qgs bucket (use one of the existing job-0058/0059 COGs as the input). curl the QGIS Server WMS GetMap and confirm the tile renders (200 + non-empty image bytes) rather than 500 + GDAL permission error. Clean up the test `.qgs` after.

4. **Document the grant** with a comment block in the Tofu citing job-0061 + layer-emission-contract.md + the publish_layer dependency.

### File ownership (exclusive)
- `infra/qgis-server.tf` OR `infra/qgis-server/buckets.tf` (wherever the existing pattern is — single-resource addition only)
- `reports/inflight/job-0061-infra-20260607/`

### FROZEN
- All other Tofu (`*.tf`)
- Cloud Run service config (no env / image / volume change)
- Service-account creation/labels (just add a bucket binding)
- All services/, packages/, web/, docs/, etc.

### Acceptance criteria
- [ ] `google_storage_bucket_iam_member` added: `qgis-server-runtime` SA × `gs://grace-2-hazard-prod-runs/` × `roles/storage.objectViewer`
- [ ] Tofu plan shows the single addition; apply succeeds clean
- [ ] `gcloud projects get-iam-policy` (filtered by SA) confirms bucket-scoped binding; no project-level grant added
- [ ] Live WMS GetMap on a test `.qgs` referencing a runs-bucket COG returns a real image (not GDAL 500)
- [ ] Comment block citing job-0061 + the contract decision
- [ ] No edits to FROZEN paths
- [ ] Single commit
