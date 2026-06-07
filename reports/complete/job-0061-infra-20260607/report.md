# Report: IAM grant — QGIS Server runtime SA reads runs bucket

**Job ID:** job-0061-infra-20260607
**Sprint:** sprint-09
**Specialist:** infra
**Task:** Grant `roles/storage.objectViewer` to the `grace-2-qgis-server` service account on `gs://grace-2-hazard-prod-runs/` so QGIS Server can read flood-depth COGs via `/vsigs/` at WMS render time.
**Status:** ready-for-audit

## Summary

Added a single `google_storage_bucket_iam_member` resource (`qgis_server_runs_viewer`) to `infra/buckets.tf`, mirroring the existing -qgs/-cog/-fgb pattern. Applied via `tofu apply -target=...`. The grant was verified bucket-scoped only (no project-level grant). Live WMS smoke confirmed QGIS Server renders a 256x256 PNG from a runs-bucket COG via `/vsigs/` without GDAL permission errors.

## Changes Made

- File: `infra/buckets.tf`
  - Added `resource "google_storage_bucket_iam_member" "qgis_server_runs_viewer"` at end of file, after the existing -qgs/-cog/-fgb bindings and the job-0021 deferred-binding note.
  - Accompanied by a comment block explaining: (a) why (WMS render of /vsigs/ runs-bucket COGs for publish_layer flow in job-0062); (b) scope is bucket-level per NFR-S-2 + zero-project-grants invariant from job-0021; (c) cites job-0061 + `docs/decisions/layer-emission-contract.md`.
  - IAM bindings co-located: the existing -qgs/-cog/-fgb objectViewer bindings are in `infra/buckets.tf` (NOT in `infra/qgis-server/buckets.tf` which does not exist -- the comment in qgis-server.tf line 13 was a kickoff-time forward reference that never materialized as a separate file).

## Tofu Plan Output (targeted)

```
$ tofu plan -target=google_storage_bucket_iam_member.qgis_server_runs_viewer

  # google_storage_bucket_iam_member.qgis_server_runs_viewer will be created
  + resource "google_storage_bucket_iam_member" "qgis_server_runs_viewer" {
      + bucket = "grace-2-hazard-prod-runs"
      + etag   = (known after apply)
      + id     = (known after apply)
      + member = "serviceAccount:grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com"
      + role   = "roles/storage.objectViewer"
    }

Plan: 1 to add, 0 to change, 0 to destroy.
```

Untargeted `tofu plan` shows 1 to add + 1 pre-existing Cloud Run scaling drift (unrelated to this job) + MongoDB Atlas 401 errors (no Atlas credentials on this machine). The -target plan isolates the single job-scoped change cleanly.

## Tofu Apply Output

```
$ tofu apply -target=google_storage_bucket_iam_member.qgis_server_runs_viewer -auto-approve

google_storage_bucket_iam_member.qgis_server_runs_viewer: Creating...
google_storage_bucket_iam_member.qgis_server_runs_viewer: Creation complete after 4s
  [id=b/grace-2-hazard-prod-runs/roles/storage.objectViewer/serviceAccount:grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com]

Apply complete! Resources: 1 added, 0 changed, 0 destroyed.
```

## IAM Verification

### Bucket-scoped binding confirmed

```
$ gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-runs --format=json | grep -A 3 'qgis-server'
        "serviceAccount:grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com",
        "serviceAccount:workflow-invoker-sfincs@grace-2-hazard-prod.iam.gserviceaccount.com"
      ],
      "role": "roles/storage.objectViewer"
```

### No project-level grant added

```
$ gcloud projects get-iam-policy grace-2-hazard-prod \
    --flatten='bindings[].members' \
    --filter='bindings.members:qgis-server' \
    --format=json
[]
```

Empty result confirms the `grace-2-qgis-server` SA has zero project-level IAM bindings. NFR-S-2 and the zero-project-grants invariant (job-0021) preserved.

## Live WMS Smoke Verification

A minimal test `.qgs` was uploaded to `gs://grace-2-hazard-prod-qgs/job0061-smoke-test.qgs` referencing the existing COG at `/vsigs/grace-2-hazard-prod-runs/01KTHTFCV5E588A293N0JFQTZH/flood_depth_peak.tif` (COG from job-0058/0059, EPSG:3857). QGIS Server mount path: `MAP=/mnt/qgs/job0061-smoke-test.qgs`.

**GetCapabilities:**
```
$ curl "...wms?SERVICE=WMS&REQUEST=GetCapabilities&MAP=/mnt/qgs/job0061-smoke-test.qgs"
-> HTTP 200, WMS_Capabilities XML, <Title>job-0061 smoke test</Title>
```

**GetMap:**
```
$ curl -v -o /tmp/wms-getmap-smoke.png \
  "...wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&MAP=/mnt/qgs/job0061-smoke-test.qgs \
  &LAYERS=flood-depth-peak&STYLES=&CRS=EPSG:3857 \
  &BBOX=409109,2936568,425279,2952348&WIDTH=256&HEIGHT=256&FORMAT=image/png"

< HTTP/2 200
< content-type: image/png
< content-length: 876

$ file /tmp/wms-getmap-smoke.png
/tmp/wms-getmap-smoke.png: PNG image data, 256 x 256, 8-bit/color RGBA, non-interlaced
```

HTTP 200, `image/png`, 876 bytes, valid 256x256 RGBA PNG confirmed by `file`. No GDAL "Permission denied", no 500. Smoke passes.

**Cleanup:** `gs://grace-2-hazard-prod-qgs/job0061-smoke-test.qgs` deleted post-verification.

## Decisions Made

- **Decision:** Applied as `-target` rather than full `tofu apply`.
  - Rationale: Untargeted plan includes pre-existing Cloud Run scaling drift and Atlas 401 errors. The `-target` apply isolates the single job-scoped IAM addition without touching FROZEN resources (Cloud Run service config). Correct approach when applying only the job-scoped change.
  - Alternatives: Full `tofu apply` -- would attempt to fix Cloud Run scaling drift (not in scope) and fail on Atlas resources.

- **Decision:** IAM bindings landed in `infra/buckets.tf`, not `infra/qgis-server/buckets.tf`.
  - Rationale: `infra/qgis-server/buckets.tf` does not exist -- the kickoff reference was a forward reference that never materialized as a separate file. The actual -qgs/-cog/-fgb bindings live in `infra/buckets.tf`. Co-locating the new -runs binding with the existing three is the correct pattern.

## Invariants Touched

- **Invariant 5 (Tier separation):** preserves -- runs bucket remains PAP-enforced, no public IAM, QGIS Server is still the only Tier B rendering path.
- **NFR-S-2 (zero-project-grants):** preserves -- bucket-scoped binding only, project IAM shows `[]` for the SA.
- **Invariant 6 (Metadata-payload pattern):** preserves -- `objectViewer` at bucket scope; MongoDB remains the only discovery path.
- **Invariant 4 (Rendering through QGIS Server):** extends -- this grant enables QGIS Server to render from the runs bucket, which is the correct path.

## Open Questions

- **Pre-existing Cloud Run scaling drift** (`google_cloud_run_v2_service.qgis_server` scaling block): present before this job, out of scope here. TENTATIVE cause: provider version change changed representation of `min_instance_count = 0`. Does not affect runtime behavior. Recommend a dedicated infra follow-up job to address.

## Dependencies and Impacts

- Depends on: job-0040 (runs bucket), job-0029 (QGIS Server service + SA), job-0021 (zero-project-grants pattern)
- Affects: job-0062 (engine -- publish_layer atomic tool): IAM prerequisite for sprint-09 publish_layer flow is now satisfied.

## Verification

- Tests run: `tofu plan -target`, `tofu apply -target`, bucket IAM policy check, project IAM policy check, WMS GetCapabilities, WMS GetMap
- Live E2E evidence:
  - Tofu apply: `1 added, 0 changed, 0 destroyed`
  - `gcloud storage buckets get-iam-policy`: SA in `roles/storage.objectViewer` on runs bucket
  - `gcloud projects get-iam-policy --filter=...qgis-server`: `[]` (no project grants)
  - WMS GetMap: HTTP 200, `image/png`, 256x256 RGBA PNG from runs-bucket COG via /vsigs/
- Results: pass
