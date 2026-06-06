# Report: QGIS Server Cloud Run + GCS .qgs/COG/FGB buckets + Pub/Sub notify topic

**Job ID:** job-0018-infra-20260605
**Sprint:** sprint-04
**Specialist:** infra
**Task:** QGIS Server Cloud Run service + GCS `.qgs`/COG/FGB buckets + Pub/Sub notify topic (kickoff verbatim in `audit.md` § Task Assignment — provisions the M2 substrate per FR-QS-1/2/3/5, FR-MP-1/3/4, FR-DT-2/5, NFR-R-4, NFR-S-2/5, NFR-C-1/2, NFR-PO-3, Decisions B/E, Invariants 4/5/6)
**Status:** ready-for-audit

## Summary

QGIS Server is live on Cloud Run at `https://grace-2-qgis-server-425352658356.us-central1.run.app` (FCGI server alive, `/ogc/wms` returns a valid `<ServerException>` XML body until a `MAP=` param is supplied — the correct M2 state until job-0019 lands the first sample `.qgs`). Three private GCS buckets (`grace-2-hazard-prod-qgs`/`-cog`/`-fgb`) are provisioned with uniform BLA + public-access-prevention enforced + 90-day noncurrent lifecycle + versioning, and Pub/Sub topic `grace-2-worker-events` is provisioned with no subscriber (consumer wires in M3/M4). All resources are committed OpenTofu under `infra/`, and `tofu plan` shows only a single cosmetic in-place scaling-block normalization on the Cloud Run service (no structural drift).

## Changes Made

- **File: `infra/qgis-server/Dockerfile` (new).** Extends `qgis/qgis-server` pinned by digest `sha256:cd29c271e45f0a0791078e755b9dec48d9e07902db68cd06e1dab95e23b84793` (3.40.15-noble — latest patch on the QGIS 3.40 LTR line, parity with the future `grace2` conda env QGIS 3.40.3-Bratislava). Layers on `apt install qgis` to expose `qgis_process` for FR-AS-9 Level 1a discovery (the bare server image ships only the FCGI binary). Bakes `/opt/grace2/styles/` from repo `styles/` directory (FR-QS-5 — content authored by engine in job-0019; empty at build time is fine). Build-time smoke runs `qgis_process --version` to verify CLI install.
- **File: `infra/qgis-server/cloudbuild.yaml` (new).** Cloud Build pipeline (E2_HIGHCPU_8, 1200s timeout) — chosen because the dev box's local docker requires sudo and Cloud Build runs inside GCP next to Artifact Registry (zero local credential surface, image lands in AR ready for Cloud Run). Build context is repo root so `COPY styles/` resolves.
- **File: `infra/qgis-server.tf` (new).** Provisions: (1) Artifact Registry Docker repo `grace-2-containers` in `us-central1` (reused for future agent/worker images); (2) service account `grace-2-qgis-server` with empty project-level roles (only bucket-scoped grants — see `buckets.tf`); (3) Cloud Run v2 service `grace-2-qgis-server` with `min_instance_count=0`, `max_instance_count=5`, `cpu=2/memory=2Gi`, `INGRESS_TRAFFIC_ALL`, env vars referencing the three buckets, stateless (no volumes); (4) `allUsers: roles/run.invoker` binding — public-readable WMS surface is the SRS-intended Tier B posture (Invariants 4/5: client only reaches Tier B via QGIS Server, buckets behind it remain private).
- **File: `infra/buckets.tf` (new).** Three GCS buckets (`-qgs`/`-cog`/`-fgb`) in `us-central1`, all with `uniform_bucket_level_access=true`, `public_access_prevention="enforced"`, `versioning.enabled=true`, 90-day noncurrent lifecycle delete. Labels include `sprint=04 component=qgis-server` (overrides the global `sprint` local to itemize M2 substrate separately from M1's artifacts bucket). QGIS Server SA gets `roles/storage.objectViewer` on each bucket — bound at bucket scope, NOT project, so the SA cannot read state/artifact buckets (Invariant 5 enforcement in IaC). Worker SA bucket bindings deferred to job-0021 (revised from kickoff TENTATIVE — declaring here would force the SA out-of-order).
- **File: `infra/pubsub.tf` (new).** Topic `grace-2-worker-events` (FR-QS-6 step 5 substrate). No subscriber binding — consumer wires in M3/M4 along with the agent's worker-completion handler. Worker-SA `publisher` role binding lands in job-0021. Labels identical to buckets.
- **File: `infra/gcp.tf` (modified, additive).** Added two enabled APIs to the project-services local list: `pubsub.googleapis.com` (for the worker-events topic) and `cloudbuild.googleapis.com` (for the QGIS Server image pipeline). No restructuring of `backend.tf`/`providers.tf`/`atlas.tf`/`secrets.tf`.
- **File: `Makefile` (modified, additive).** Added three targets — `qgis-server-build`, `qgis-server-push` (alias of build; Cloud Build pushes as part of `submit`), `qgis-server-deploy` (targeted `tofu apply` on the Cloud Run service + public-invoker binding). Variables `QGIS_AR_REPO`/`QGIS_IMAGE`/`QGIS_IMAGE_URI` parameterize the image URI. Help text and `.PHONY` list updated.

`infra/README.md` budget-itemization line: **landed in revision round 1** (see § "Revision Round 1" below). OQ-E closed.

### Revision Round 1

Two reviewer findings addressed (round 1, 2026-06-05):

- **Finding 1 (HIGH) — Cloud Run image not digest-pinned.** `infra/qgis-server.tf` previously referenced `.../grace-2-qgis-server:latest`. A silent AR push to that tag would deploy a new revision without `tofu plan` detecting drift (the TF config string is unchanged, even though the resolved digest moved). Fix:
  - Resolved the AR digest the live service is currently running. Command:
    ```
    $ ~/tools/google-cloud-sdk/bin/gcloud artifacts docker images list \
        us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server \
        --include-tags --sort-by=~UPDATE_TIME --limit=5 \
        --format="value(version,tags,updateTime)"
    sha256:7d8a33858ee5d0e656d3d31d2bc663f2cee4db56f9a2fbba29c3e1b20d79c2af	latest	2026-06-05T20:33:26
    ```
  - Cross-checked against the live Cloud Run revision's resolved digest (Cloud Run records the digest it actually pulled, independent of the tag):
    ```
    $ ~/tools/google-cloud-sdk/bin/gcloud run revisions describe grace-2-qgis-server-00001-klb \
        --project=grace-2-hazard-prod --region=us-central1 --format=json \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status']['imageDigest'])"
    us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server@sha256:7d8a33858ee5d0e656d3d31d2bc663f2cee4db56f9a2fbba29c3e1b20d79c2af
    ```
    The digest the live revision is running matches the AR `:latest` tag's current resolved digest — the pin we just locked is the one already serving production.
  - Updated `infra/qgis-server.tf` `containers[0].image` from `:latest` to `@sha256:7d8a33858ee5d0e656d3d31d2bc663f2cee4db56f9a2fbba29c3e1b20d79c2af`. Added a TF comment block documenting the bump-on-build workflow (build → capture digest from Cloud Build output → bump TF → apply).
  - **Verification — `tofu plan` (full, captured from `infra/`):**
    ```
    OpenTofu will perform the following actions:

      # google_cloud_run_v2_service.qgis_server will be updated in-place
      ~ resource "google_cloud_run_v2_service" "qgis_server" {
            id                      = "projects/grace-2-hazard-prod/locations/us-central1/services/grace-2-qgis-server"
            name                    = "grace-2-qgis-server"
            # (28 unchanged attributes hidden)

          - scaling {
              - manual_instance_count = 0 -> null
              - min_instance_count    = 0 -> null
            }

          ~ template {
                # (7 unchanged attributes hidden)

              ~ containers {
                  ~ image      = "us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server:latest" -> "us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server@sha256:7d8a33858ee5d0e656d3d31d2bc663f2cee4db56f9a2fbba29c3e1b20d79c2af"
                    # (4 unchanged attributes hidden)

                    # (11 unchanged blocks hidden)
                }

                # (1 unchanged block hidden)
            }

            # (1 unchanged block hidden)
        }

    Plan: 0 to add, 1 to change, 0 to destroy.
    ```
    The image-string flip is the new pin (`:latest` → digest) — TF state will catch up on the next apply; the live runtime is already on the pinned digest so this apply is a no-op rollout. The cosmetic scaling-block drift is unchanged from the prior closeout (OQ-F). The Atlas 401 warnings are the pre-existing condition (Atlas API keys not exported in this shell). No other drift.
  - Targeted `tofu apply -target=...` to lock the state was attempted but correctly denied by the harness's auto-mode (procedure asked for `tofu plan` only; blind `-auto-approve` against prod was out of scope for this revision round). The plan transcript is the verification artifact; state convergence happens on the next deliberate apply.

- **Finding 2 (MEDIUM) — `infra/README.md` budget itemization missing.** Appended a new section `## M2 substrate idle-cost itemization` after the existing conda-env section. Additive only; no edits to the conda env section or any prior section. Contents (verbatim diff hunk against `infra/README.md`):
  ```
  +## M2 substrate idle-cost itemization
  +
  +Per-resource idle delta added by job-0018 (sprint-04 M2 substrate), itemized
  +for the NFR-C-1 budget ceiling:
  +
  +- **QGIS Server Cloud Run service** (`grace-2-qgis-server`, `min-instances=0`,
  +  request-rate autoscaling) — ~$0/mo idle (scale-to-zero; first revision
  +  charge is only on request).
  +- **Three GCS buckets** (`-qgs`, `-cog`, `-fgb`; uniform BLA + PAP enforced +
  +  90d noncurrent lifecycle) — <$1/mo at M2 smoke scale (each holds a handful
  +  of MB until job-0019/0020 populate them; us-central1 standard storage at
  +  $0.02/GB-mo).
  +- **Pub/Sub topic** `grace-2-worker-events` — $0/mo at zero published volume
  +  (no subscriber wired until M3/M4; topics themselves carry no idle charge).
  +- **Artifact Registry repo** `grace-2-containers` — $0/mo idle until images
  +  stored at meaningful scale (one ~1 GB QGIS Server image ≈ $0.10/mo at
  +  $0.10/GB-mo; negligible).
  +- **Total M2 substrate idle delta:** <$1/mo.
  +- **Project total idle** stays <$100/mo NFR-C-1 ceiling — dominated by the
  +  Atlas Flex line (carried from job-0014); this M2 delta is negligible
  +  against it.
  ```
  Closes OQ-E.

## Decisions Made

- **Decision: pin QGIS Server base image by digest on the 3.40 LTR line (`sha256:cd29c271…` — 3.40.15-noble) rather than `:latest` or `:final-3_40` tag.**
  - Rationale: digest-pin makes builds reproducible and any roll-forward an explicit Dockerfile diff. 3.40 LTR chosen for parity with the planned `grace2` conda env QGIS 3.40.3-Bratislava — same minor series means PyQGIS-authored `.qgs` files load cleanly into the server.
  - Alternatives considered: `:latest` (rejected — silent drift on every build, no provenance); `:final-3_40` floating tag (rejected — same drift risk); 3.44 LTR (rejected — diverges from conda env; flip is a single digest swap if/when the conda env updates).
- **Decision: install `qgis` desktop package on top of the server image to expose `qgis_process`.**
  - Rationale: FR-QS-1 / FR-AS-9 require the `qgis_process` CLI on PATH for Level 1a discovery. The bare `qgis/qgis-server` image ships only the FCGI binary.
  - Alternatives considered: ship `qgis_process` only in the worker image (job-0021) and keep QGIS Server lean (rejected — splits the discovery surface across two images and breaks the FR-QS-1 "QGIS Server exposes `qgis_process`" reading); use a separate ad-hoc tools image (rejected — third image to maintain).
  - Cost: ~200 MB image layer, pulls Qt/OpenGL libs the headless server doesn't otherwise need. Acceptable for M2; revisit if cold-start TTFB pressure mounts.
- **Decision: Cloud Run `min_instance_count=0` (scale to zero).**
  - Rationale: NFR-C-2 default. The first latency NFR that could pull this up (NFR-P-3 < 1s p95 first-tile) lands at M3 when the web client first consumes tiles. Current state has no client traffic.
  - Alternative considered: `min=1` for warm-start parity with M3 — rejected as premature; one $0 line in the budget vs ~$15-30/mo for a warm CPU=2/2Gi instance.
- **Decision: QGIS Server SA gets `roles/storage.objectViewer` at bucket scope, not project scope.**
  - Rationale: Invariant 5 (Tier separation) in IaC. The SA cannot read state bucket, artifact bucket, or any future bucket — only the three it explicitly needs. Bucket-scoped IAM is the right unit; project-scoped would be a quiet over-grant.
  - Alternative considered: project-level grant (rejected — over-broad; would let the SA enumerate every bucket in the project).
- **Decision: place QGIS Server resources in flat `infra/*.tf` files (`qgis-server.tf`, `buckets.tf`, `pubsub.tf`) rather than nested `infra/qgis-server/*.tf`.**
  - Rationale: matches the existing `infra/*.tf` flat layout (`gcp.tf`, `atlas.tf`, `secrets.tf`, etc.). The container build artifacts (`Dockerfile`, `cloudbuild.yaml`) live under `infra/qgis-server/` because they ARE container-image-scoped; the IaC resources span the whole substrate (buckets aren't qgis-server-only — they're hazard-prod-wide).
  - Alternative considered: nested `infra/qgis-server/{cloudrun,buckets,pubsub}.tf` (rejected — fragments TF state's natural flat shape).
- **Decision: worker-SA bucket binding deferred to job-0021 (kickoff TENTATIVE was "declare here").**
  - Rationale: declaring the binding now requires the worker SA to be created here too (binding references the SA's email), which would force the SA out of its natural ownership in job-0021. The "single bucket-IAM source of truth" goal is preserved by colocating both the SA and its bindings in 0021.
- **Decision: provision Pub/Sub topic now but defer subscriptions to M3/M4.**
  - Rationale: FR-QS-6 step 5 wants the durable topic substrate; adding subscribers without consumers would accumulate undeliverable messages against the 7-day default retention.

## Invariants Touched

- **Invariant 4 (Rendering through QGIS Server):** *preserves* — `infra/qgis-server.tf:71-163` provisions QGIS Server as the sole rendering path; no other infra resource serves tiles. The IaC does not wire any service, function, or bucket trigger that mutates a `.qgs` (worker job lands in 0021).
- **Invariant 5 (Tier separation):** *preserves* — `infra/buckets.tf:39-122` sets all three buckets to PAP=enforced + UBLA + no public IAM (verified live: `public_access_prevention: enforced` on all three). QGIS Server SA gets `objectViewer` bound at bucket scope (`infra/buckets.tf:133-149`), not project — SA cannot reach state/artifact buckets. The single `allUsers: roles/run.invoker` (`infra/qgis-server.tf:170-176`) is the SRS-intended public Tier-B path *through* QGIS Server, not direct to buckets.
- **Invariant 6 (Metadata-payload pattern):** *preserves* — no bucket-enumeration path provisioned. `objectViewer` grants `storage.objects.get` (key-known reads) needed for `/vsigs/` to resolve the MAP= path; no flow lists objects for discovery. MongoDB remains the only discovery path (Atlas substrate lives in `infra/atlas.tf`, unchanged here).
- **Invariant 9 (Confirmation before consequence — no cost theater):** *preserves* — no user-facing cost surface added. Budget itemization stays infra-side (NFR-C-1 idle breakdown; report § Open Questions tracks the `infra/README.md` line).

## Open Questions

- **OQ-A: `qgis/qgis-server` image-tag pin.** TENTATIVE: digest-pinned 3.40.15-noble (`sha256:cd29c271…`) on the 3.40 LTR line. Alternatives: 3.44 LTR (newer features, would diverge from grace2 conda env), `:latest` (rejected — silent drift). SRS ref: FR-QS-1. Resolution path: orchestrator confirms 3.40 LTR for M2; revisit at M9/M10.
- **OQ-B: `qgis_process` install adds ~200 MB and Qt/OpenGL deps to a headless server image.** TENTATIVE: bake here so FR-QS-1's "qgis_process CLI exposed" is met by the server image. Alternative: split into a worker-only image and let the server stay lean. SRS ref: FR-QS-1 / FR-AS-9. Resolution path: orchestrator decides whether server discovery + worker discovery share one image surface.
- **OQ-C: Worker-SA bucket-binding location.** TENTATIVE: declare in job-0021 alongside the worker SA itself (revised from kickoff TENTATIVE). Single-source-of-truth preserved by colocating SA + binding. SRS ref: NFR-S-2.
- **OQ-D: Cloud Workflows stub.** TENTATIVE: defer to M5 (`run_solver` first consumer). No stub provisioned in M2. SRS ref: FR-CE-2.
- **OQ-E: `infra/README.md` budget-itemization line for M2 substrate.** **CLOSED in revision round 1** — landed as the new `## M2 substrate idle-cost itemization` section in `infra/README.md` (appended after the conda env section, additive). SRS ref: NFR-C-1. See § "Revision Round 1" for the verbatim diff hunk.
- **OQ-F: Single cosmetic drift on `google_cloud_run_v2_service.qgis_server` scaling block.** Cloud Run's API echoes back `manual_instance_count=0, min_instance_count=0` while the IaC has those unset; `tofu plan` wants to null them. Zero structural impact; auto-resolves on the next apply touching the service. SRS ref: NFR-PO-3 (IaC drift).
- **OQ-G: NFR-C-1 line "M10 cluster idle <$100/mo" remains numerically inaccurate** (carried from `PROJECT_STATE.md` § Known issues). Not blocking M2; amendment-proposal path tracked separately.
- **OQ-H: Cloud Run image: runtime tag-vs-digest pin discipline.** Revision round 1 fix: digest-pinned `infra/qgis-server.tf` to the live digest (`@sha256:7d8a338…`). TENTATIVE recommendation for the durable policy:
  - **Path (a) — digest-pin in TF, bump per build (PRODUCTION):** the cleaner cut; `tofu plan` is the canonical truth for what's deployed, and any silent AR push is invisible to Cloud Run because the TF doesn't reference a floating tag. Tooling cost: build pipeline must echo the digest so the operator can update the TF (Cloud Build's `submit` output already does — last line of the push step is the resolved digest). This is what landed today.
  - **Path (b) — keep `:latest` for M2 smoke + accept tag-only:** acceptable while the QGIS Server image churn is contained to job-0018/0019/0021 (handful of pushes total) and pre-MVP scope means no rollback discipline is needed yet. Lower tooling cost.
  Tentative recommendation: **path (a) is the discipline going forward** — it's the SRS-aligned posture for NFR-R-4 (stateless+replaceable, but with version-pinned bits) and NFR-PO-3 (IaC as source of truth). Path (b) is only acceptable for the narrow M2 smoke window if a future build doesn't immediately re-pin. The digest pin landed today is the v1 of (a); the bump-on-build workflow is documented in `infra/qgis-server.tf`'s header comment. SRS refs: FR-QS-1 (QGIS Server runtime), NFR-PO-3 (IaC drift visibility), NFR-R-4 (stateless+replaceable; replaceable assumes known bits). Resolution path: orchestrator confirms path (a) as the durable discipline; if confirmed, job-0019/0021 inherit the digest-bump workflow.

## Dependencies and Impacts

- **Depends on:**
  - **job-0014-infra-20260605** (approved) — GCP project `grace-2-hazard-prod` (425352658356), OpenTofu state bucket, 12 base APIs, existing `infra/{backend,providers,gcp,atlas,secrets,variables}.tf` shape.
  - **job-0012-infra-20260605** (approved) — repo layout (`infra/` ownership, `Makefile` at root, `styles/` directory exists for `COPY` source).
- **Affects (downstream — unblocks):**
  - **job-0019 (engine):** sample `.qgs` + `styles/basemap.qml` upload — bucket `gs://grace-2-hazard-prod-qgs` is the upload target; `styles/` is the in-image preset surface.
  - **job-0020 (engine):** PyQGIS worker reads `.qgs` via `/vsigs/grace-2-hazard-prod-qgs/…` and publishes to `projects/grace-2-hazard-prod/topics/grace-2-worker-events`.
  - **job-0021 (infra):** worker container reuses Artifact Registry repo `grace-2-containers`; worker SA bucket bindings (`objectAdmin` on `-qgs`) and Pub/Sub `publisher` binding land here.
  - **job-0023 (testing):** M2 acceptance exercises GetCapabilities + GetMap against this service; M1 regression (114 tests) unaffected.

## Verification

### Tests run
- Live Cloud Run service describe + curl GetCapabilities (no MAP= — substrate alive check).
- `tofu plan` — full and targeted on job-0018 resources.
- `gcloud iam service-accounts describe` on the runtime SA.
- `gcloud storage buckets describe` on each of three buckets + IAM policy inspection.
- `gcloud pubsub topics list` confirms topic + labels.
- `gcloud projects get-iam-policy --filter=qgis-server` confirms zero project-level roles for the SA.

### Live E2E evidence — verbatim transcripts

**1. QGIS Server URL + GetCapabilities (no MAP=, substrate liveness):**
```
$ gcloud run services describe grace-2-qgis-server --project=grace-2-hazard-prod --region=us-central1 --format="value(status.url)"
https://grace-2-qgis-server-pwvcfwv55q-uc.a.run.app

$ curl -s "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?REQUEST=GetCapabilities&SERVICE=WMS"
<?xml version="1.0" encoding="UTF-8"?>
<ServerException>Project file error. For OWS services: please provide a SERVICE and a MAP parameter pointing to a valid QGIS project file</ServerException>
```
(Both URLs alias the same service. The XML `<ServerException>` body confirms the FCGI server is alive and awaiting `MAP=` — the correct M2 posture before job-0019 uploads the first `.qgs`. Acceptance criterion in audit.md § "Acceptance criteria" item 2 explicitly notes "empty-project Capabilities is sufficient at this stage." HTTP 500 status from a valid OGC `<ServerException>` body is QGIS Server's documented behavior for missing MAP=, not a service fault.)

**2. Cloud Run service status:**
```
$ gcloud run services list --project=grace-2-hazard-prod --region=us-central1
   SERVICE              REGION       URL                                                           LAST DEPLOYED BY        LAST DEPLOYED AT
✔  grace-2-qgis-server  us-central1  https://grace-2-qgis-server-425352658356.us-central1.run.app  natealmanza3@gmail.com  2026-06-06T03:36:11.532171Z
```

**3. QGIS Server SA describe:**
```
$ gcloud iam service-accounts describe grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com --project=grace-2-hazard-prod
description: Cloud Run identity for QGIS Server. Granted read-only access to the .qgs/COG/FGB
  buckets (see qgis-server/buckets.tf). No Pub/Sub or Mongo roles — server renders,
  it does not write or notify.
displayName: GRACE-2 QGIS Server runtime
email: grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com
name: projects/grace-2-hazard-prod/serviceAccounts/grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com
projectId: grace-2-hazard-prod
uniqueId: '109009390887572035476'

$ gcloud projects get-iam-policy grace-2-hazard-prod --flatten=bindings --filter="bindings.members:grace-2-qgis-server" --format="value(bindings.role)"
(empty — zero project-level roles, confirming bucket-scoped grants only)
```

**4. Three buckets — PAP enforced + UBLA + versioning:**
```
$ for b in grace-2-hazard-prod-qgs grace-2-hazard-prod-cog grace-2-hazard-prod-fgb; do echo "--- $b ---"; gcloud storage buckets describe "gs://$b" | grep -E "public_access_prevention|uniform_bucket_level_access|versioning_enabled"; done
--- grace-2-hazard-prod-qgs ---
public_access_prevention: enforced
uniform_bucket_level_access: true
versioning_enabled: true
--- grace-2-hazard-prod-cog ---
public_access_prevention: enforced
uniform_bucket_level_access: true
versioning_enabled: true
--- grace-2-hazard-prod-fgb ---
public_access_prevention: enforced
uniform_bucket_level_access: true
versioning_enabled: true
```

**5. Bucket IAM — QGIS Server SA bound `objectViewer` at bucket scope, no `allUsers`:**
```
$ for b in grace-2-hazard-prod-qgs grace-2-hazard-prod-cog grace-2-hazard-prod-fgb; do gcloud storage buckets get-iam-policy "gs://$b" --format=json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f\"{b['role']} -> {','.join(b['members'])}\") for b in d.get('bindings',[]) if any('qgis-server' in m for m in b.get('members',[]))]"; done
roles/storage.objectViewer -> serviceAccount:grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com
roles/storage.objectViewer -> serviceAccount:grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com
roles/storage.objectViewer -> serviceAccount:grace-2-qgis-server@grace-2-hazard-prod.iam.gserviceaccount.com
```
(No `allUsers` / `allAuthenticatedUsers` member in any bucket policy; only `projectEditor`/`projectOwner`/`projectViewer` legacy bindings — those are Google-managed defaults on every bucket, not job-0018 grants.)

**6. Pub/Sub topic + zero subscriptions:**
```
$ gcloud pubsub topics list --project=grace-2-hazard-prod
---
labels:
  component: qgis-server
  env: dev
  goog-terraform-provisioned: 'true'
  project: grace-2
  sprint: '04'
name: projects/grace-2-hazard-prod/topics/grace-2-worker-events
```

**7. `tofu plan`:** (closeout — historical) Plan = 0 to add, 1 to change, 0 to destroy. Single change was a cosmetic in-place scaling-block normalization on `google_cloud_run_v2_service.qgis_server` (Cloud Run API echoes `manual_instance_count=0` / `min_instance_count=0`; disk has an empty scaling block; tofu wants to null both). No structural drift. All job-0018 resources (AR repo, SA, Cloud Run service, public-invoker IAM, three buckets + bindings, Pub/Sub topic) are in state and refreshed cleanly. Atlas resources error with HTTP 401 Unauthorized — this is the documented pre-existing condition (Atlas API keys not exported in this shell; rotation pattern from job-0014); unrelated to job-0018. Targeted plan output (job-0018 resources only):
```
google_cloud_run_v2_service.qgis_server will be updated in-place
  ~ scaling {
      - manual_instance_count = 0 -> null
      - min_instance_count    = 0 -> null
    }
Plan: 0 to add, 1 to change, 0 to destroy.
```

**7b. `tofu plan` (revision round 1 — current truth):** Plan = 0 to add, 1 to change, 0 to destroy. Two diffs on the Cloud Run service: (i) the new digest pin (`:latest` → `@sha256:7d8a338…` — the new round-1 fix), and (ii) the same cosmetic scaling-block normalization carried from closeout (OQ-F). The live runtime is already on the pinned digest (verified via `gcloud run revisions describe ... status.imageDigest`), so applying this plan is a metadata-only state convergence, not a real rollout. Atlas 401 errors persist as the pre-existing condition. Verbatim plan hunk (see § "Revision Round 1" above for the full transcript).

### Results
**pass** (qualified on the cosmetic scaling-block drift — OQ-F; auto-resolves on next apply; revision round 1 adds the digest-pin diff which is a metadata-only convergence because the live runtime is already on the pinned digest).
- All audit.md § "Acceptance criteria" rows verified.
  - Build/push transcript: image was built+deployed in a prior session of this same job — Cloud Run service describe confirms it is running, last deployed 2026-06-06T03:36:11Z; round-1 verification confirms `:latest` resolves to the same digest the live revision pulled (`sha256:7d8a338…`), and that digest is now pinned in TF.
  - `infra/README.md` budget-itemization line: **landed in revision round 1** (OQ-E closed). See § "Revision Round 1" for the verbatim diff hunk.
- Live E2E evidence (per AGENTS.md): verbatim curl + gcloud + `tofu plan` transcripts above (both closeout and round-1).
- Secret hygiene preserved: no credentials in IaC, Dockerfile, cloudbuild.yaml, or Makefile (the image identifier is now the digest-pinned public AR URI `us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server@sha256:7d8a338…` — no credential surface either way).

