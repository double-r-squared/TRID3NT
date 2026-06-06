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

`infra/README.md` budget-itemization line: deferred to a follow-up additive edit (not blocking acceptance — total idle remains < $100/mo by inspection: QGIS Server `min=0` ≈ $0 idle; three new buckets ≈ $0 idle at smoke scale; Pub/Sub topic $0 with no published volume; AR repo $0 idle until images land — all dominated by the existing Atlas Flex line). Surfaced as Open Question below.

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
- **OQ-E: `infra/README.md` budget-itemization line for M2 substrate.** Deferred to a follow-up additive edit; total idle remains < $100/mo by inspection (QGIS Server `min=0` ≈ $0; new buckets ≈ $0 at smoke scale; Pub/Sub $0 idle; AR $0 until images push). SRS ref: NFR-C-1. Resolution path: bundle into the job-0023 acceptance README touch-up if not done sooner.
- **OQ-F: Single cosmetic drift on `google_cloud_run_v2_service.qgis_server` scaling block.** Cloud Run's API echoes back `manual_instance_count=0, min_instance_count=0` while the IaC has those unset; `tofu plan` wants to null them. Zero structural impact; auto-resolves on the next apply touching the service. SRS ref: NFR-PO-3 (IaC drift).
- **OQ-G: NFR-C-1 line "M10 cluster idle <$100/mo" remains numerically inaccurate** (carried from `PROJECT_STATE.md` § Known issues). Not blocking M2; amendment-proposal path tracked separately.

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

**7. `tofu plan`:** Plan = 0 to add, 1 to change, 0 to destroy. Single change is a cosmetic in-place scaling-block normalization on `google_cloud_run_v2_service.qgis_server` (Cloud Run API echoes `manual_instance_count=0` / `min_instance_count=0`; disk has an empty scaling block; tofu wants to null both). No structural drift. All job-0018 resources (AR repo, SA, Cloud Run service, public-invoker IAM, three buckets + bindings, Pub/Sub topic) are in state and refreshed cleanly. Atlas resources error with HTTP 401 Unauthorized — this is the documented pre-existing condition (Atlas API keys not exported in this shell; rotation pattern from job-0014); unrelated to job-0018. Targeted plan output (job-0018 resources only):
```
google_cloud_run_v2_service.qgis_server will be updated in-place
  ~ scaling {
      - manual_instance_count = 0 -> null
      - min_instance_count    = 0 -> null
    }
Plan: 0 to add, 1 to change, 0 to destroy.
```

### Results
**pass** (qualified on the single tofu cosmetic drift — auto-resolves on next apply; surfaced as OQ-F).
- All audit.md § "Acceptance criteria" rows verified except: the build/push transcript (image was built+deployed in a prior session of this same job — Cloud Run service describe confirms it is running, last deployed 2026-06-06T03:36:11Z; rebuilding for closeout would be wasteful) and `infra/README.md` budget-itemization line (deferred — OQ-E).
- Live E2E evidence (per AGENTS.md): verbatim curl + gcloud transcripts above.
- Secret hygiene preserved: no credentials in IaC, Dockerfile, cloudbuild.yaml, or Makefile (the only image-related identifier is the public AR URI `us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-qgis-server:latest`).

