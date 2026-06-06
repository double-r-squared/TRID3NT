# Report: PyQGIS worker container Dockerfile + image build/push + Cloud Run Job deployment

**Job ID:** job-0021-infra-20260605
**Sprint:** sprint-04
**Specialist:** infra
**Task:** PyQGIS worker container Dockerfile + image build/push + Cloud Run Job deployment (FR-QS-6 worker substrate). Build a container that extends the QGIS Server 3.40 LTR base, packages services/workers/pyqgis (job-0020) + styles/ (job-0019), runs `python -m services.workers.pyqgis`. Deploy as a Cloud Run v2 Job named `grace-2-pyqgis-worker` with a dedicated runtime SA narrowly scoped to `roles/storage.objectAdmin` on the -qgs bucket + `roles/pubsub.publisher` on the worker-events topic, mounting the -qgs bucket WRITABLE at `/mnt/qgs` (mirror of the job-0024 QGIS Server read-only mount pattern). Live E2E: run the Job end-to-end against a fresh copy of the sample `.qgs` and verify mutation + Pub/Sub publish.
**Status:** ready-for-audit

## Summary

Built and deployed the canonical PyQGIS worker Cloud Run Job: container under `infra/worker/Dockerfile` extends the same `qgis/qgis-server` digest the QGIS Server (job-0018/0024) uses, adds Python + PyQGIS bindings + google-cloud-{storage,pubsub}; image pushed to AR by Cloud Build; pinned by digest in `infra/worker.tf` (root) which provisions the SA + bucket-scoped objectAdmin + topic-scoped publisher + Cloud Run v2 Job with the -qgs bucket mounted writable at `/mnt/qgs`. Live E2E succeeded — `gcloud run jobs execute` against `gs://grace-2-hazard-prod-qgs/worker-test-input.qgs` returned exit 0; structured worker envelope `{layers_before:['basemap-osm-conus'], layers_after:['basemap-osm-conus','container-test-layer'], status:'ok', notify_message_id:'19943000011589039'}` published to the topic and pulled back via a temp subscription; downloaded post-mutation `.qgs` shows 2 layers with both names. One diagnostic-then-fix cycle hit during execution (first run SEGV'd at `QgsApplication([], False)` ctor; diagnosis: missing `QT_QPA_PLATFORM=offscreen` env var; container image rebuilt and re-pinned).

## Changes Made

- `infra/worker/Dockerfile` — extends qgis/qgis-server@sha256:cd29c271 (same digest as job-0018). Installs python3 + python3-pip + python3-qgis; pip-installs google-cloud-storage>=2.18 + google-cloud-pubsub>=2.21 via --break-system-packages. Copies services/workers/pyqgis/ to /opt/grace2/... and styles/ to /opt/styles/ (matches STYLE_PRESET_CONTAINER_PATH in worker.py:92). WORKDIR=/opt/grace2, PYTHONPATH=/opt/grace2, ENTRYPOINT ["python3","-m","services.workers.pyqgis"]. Env: GCP_PROJECT, PUBSUB_TOPIC, CPL_MACHINE_IS_GCE=YES, CPL_GS_USE_INSTANCE_PROFILE=YES, GDAL_HTTP_USERAGENT, QT_QPA_PLATFORM=offscreen. Build-time smokes confirm PyQGIS import + google.cloud import + worker import + QML preset baked.
- `infra/worker/cloudbuild.yaml` — Cloud Build pipeline mirroring infra/qgis-server/cloudbuild.yaml. E2_HIGHCPU_8, 1800s timeout.
- `infra/worker.tf` — Cloud Run v2 Job + IAM. Resources: google_service_account.pyqgis_worker (pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com), google_storage_bucket_iam_member.pyqgis_worker_qgs_admin (roles/storage.objectAdmin on -qgs bucket, bucket-scoped), google_pubsub_topic_iam_member.pyqgis_worker_publisher (roles/pubsub.publisher on grace-2-worker-events topic, topic-scoped), google_cloud_run_v2_job.pyqgis_worker (name grace-2-pyqgis-worker, region us-central1, parallelism=1, task_count=1, max_retries=0, timeout 900s, 2 CPU / 2 Gi memory, image digest-pinned to sha256:fffd7e0f41aa255c80ff288e19cf950e5953e05cc79cc67524dbc9c7edbcacd9). Env on Job: QGS_URI="", LAYER_TO_ADD="", GCP_PROJECT, GOOGLE_CLOUD_PROJECT, PUBSUB_TOPIC, GDAL VSI auth env vars. Mount: volumes.gcs{bucket=grace-2-hazard-prod-qgs, read_only=false} + volume_mounts{mount_path=/mnt/qgs} — writable, mirror of job-0024 pattern.
- `infra/worker-variables.tf` — explicit-empty placeholder for future worker-scoped vars.
- `Makefile` — additive targets: worker-build, worker-push (alias), worker-deploy, worker-run-job (QGS_URI=... LAYER=... required). No existing target modified.

## Decisions Made

- Decision: TF placement — infra/worker.tf at infra root (not infra/worker/*.tf). Rationale: OpenTofu loads *.tf from the working dir only, not subdirs. Followed job-0018 precedent (infra/qgis-server.tf at root alongside its infra/qgis-server/Dockerfile). Alternatives: separate module (over-engineered for 1 Job + 1 SA + 2 IAM), inline into gcp.tf (collapses ownership).
- Decision: Extend qgis/qgis-server base image vs python:3.12+conda-forge. Rationale: same Qt/GDAL/QGIS digest as the server eliminates a "server reads .qgs that worker wrote in different version" drift. Base already carries libqgis-*; adding python3-qgis completes the surface. Trade-off: image carries FCGI+nginx+xvfb the Job never uses — acceptable at M2.
- Decision: Writable /mnt/qgs mount via Cloud Run gen2 native GCS volume mount (job-0024 pattern, read_only=false). Rationale: explicitly recommended in job-0020 audit. Worker IS the only .qgs writer (Invariant 4); objectAdmin grant authorizes writes; read_only=false removes the runtime guard. Alternative SDK-only path rejected — leaves the worker's /mnt/qgs branch as dead code; mixed mount+SDK path forks the write surface.
- Decision: SA scoping — pyqgis-worker-runtime with bucket-scoped objectAdmin + topic-scoped publisher ONLY. Rationale: NFR-S-2 + Invariants 5/6. Live-verified: zero project-level bindings. Alternative project-level read rejected (enables enumeration of other buckets, violates Invariant 6).
- Decision: Env-var injection pattern — QGS_URI="" + LAYER_TO_ADD="" empty defaults on Job template, actual values via --args at exec time. Rationale: makes contract surface visible in `gcloud run jobs describe` while avoiding baked-in test values.
- Decision: QT_QPA_PLATFORM=offscreen baked into Dockerfile (not Cloud Run env). Rationale: it's a property of the container — without it QgsApplication() segfaults; baking it makes the container portable across local docker + Cloud Run. Diagnosed live from first-build SEGV.

## Invariants Touched

- Invariant 4 (Rendering through QGIS Server / PyQGIS-only .qgs writer): preserves — this Cloud Run Job IS the sanctioned writer. Writable /mnt/qgs mount is the worker's writer surface; the QGIS Server keeps its read-only mount (job-0024).
- Invariant 5 (Tier separation): preserves — bucket binding at resource scope; no project-level grants; objectAdmin is bucket-scoped, so SA cannot enumerate or write to -cog/-fgb/-artifacts.
- Invariant 6 (Metadata-payload pattern): preserves — Pub/Sub envelope IS the metadata channel; .qgs IS the payload; no bucket enumeration.
- Invariant 2 (Deterministic workflows): preserves — worker code (frozen from job-0020) has zero LLM imports.
- NFR-S-2 / NFR-S-5: preserves — no allUsers grant; SA has no JSON key; image carries no embedded credentials.
- NFR-C-2 (Cloud Run Jobs scale to zero): preserves — Jobs are inherently scale-to-zero.

## Open Questions

- OQ-21A — Image QGIS version drift vs grace2 env (TENTATIVE: accept). Container's apt install resolves to QGIS 3.44.11-Solothurn (live envelope confirms), not 3.40 LTR matching the grace2 conda env. Production read+write are both same-image 3.44; grace2 is dev-only; QgsProject is forward-compat within 3.x. If dev-env 3.40 fails to load worker output, upgrade grace2 to 3.44 (or apt-mark hold the container to 3.40).
- OQ-21B — Dockerfile carries ~600 MB Qt/GDAL duplication from python3-qgis on top of base (TENTATIVE: accept). Image ~3 GB. M2 cold-start dominated by gcsfuse mount + Python startup, not image pull from regional AR. Revisit M5+ if churn becomes operational.
- OQ-21C — QT_QPA_PLATFORM=offscreen baked vs Cloud Run env (TENTATIVE: baked). Property of the container — without it segfaults; baking makes it portable.
- OQ-21D — TF placement: infra/worker.tf at root vs infra/worker/main.tf as module (TENTATIVE: root flat). Job-0018 precedent + uniform state shape; module dance over-engineers M2's surface.
- OQ-21E — Worker SA bucket binding: objectAdmin vs objectViewer+objectCreator split (TENTATIVE: objectAdmin). Splitting adds zero security benefit; bucket-level scope IS the security boundary.
- OQ-21F — Cloud Workflows definition stub deferred per kickoff. Matches job-0018 OQ-D.

## Dependencies and Impacts

- Depends on: job-0018 (AR repo, GCS -qgs bucket, Pub/Sub topic, qgis-server base digest), job-0019 (sample .qgs + basemap.qml), job-0020 (worker module + CLI), job-0022 (local grace2 env reference), job-0024 (Cloud Run gen2 GCS mount pattern — replicated WRITABLE).
- Affects: job-0023 (M2 acceptance) — worker-run-job target ready; future agent integration M3/M4 — worker SA bindings are the contract; future M5 SFINCS solver — this IaC is the template.

## Verification

### Toolchain
```
$ uname -a
Linux maturin 6.12.74+deb13+1-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.74-2 (2026-03-08) x86_64 GNU/Linux
$ gcloud --version | head -2
Google Cloud SDK 571.0.0
bq 2.1.32
$ tofu version | head -2
OpenTofu v1.12.1
on linux_amd64
$ docker --version
Docker version 29.3.1, build c2be9cc
```
(Docker not used directly — Cloud Build is the build path.)

### Cloud Build (final image)
```
$ gcloud builds submit --project=grace-2-hazard-prod \
    --config=infra/worker/cloudbuild.yaml \
    --substitutions=_REGION=us-central1,_AR_REPO=grace-2-containers,_IMAGE=grace-2-pyqgis-worker .
ID                                    DURATION  STATUS
7649cfde-0b70-4d3e-9899-fc40a995287b  1M45S     SUCCESS

$ gcloud artifacts docker images list us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers --include-tags | grep pyqgis-worker
sha256:3acc7aa7…              (build 1, SEGV — first-exec diagnosis)
sha256:fffd7e0f41aa255c80ff…  latest   (build 2, with QT_QPA_PLATFORM=offscreen, pinned in TF)
```

### tofu plan post-apply (worker resources scope)
```
$ tofu plan -target=google_service_account.pyqgis_worker \
            -target=google_storage_bucket_iam_member.pyqgis_worker_qgs_admin \
            -target=google_pubsub_topic_iam_member.pyqgis_worker_publisher \
            -target=google_cloud_run_v2_job.pyqgis_worker
No changes. Your infrastructure matches the configuration.
```
Full tofu plan (no -target) still surfaces the OQ-F cosmetic scaling-block normalization drift on google_cloud_run_v2_service.qgis_server (job-0018 carry-over) + Atlas API-key 401s (no MONGODB_ATLAS_*_KEY env vars in this session). Neither is job-0021 scope; worker resources clean.

### Live execution — Cloud Run Job
```
$ gcloud storage cp gs://grace-2-hazard-prod-qgs/grace2-sample.qgs gs://grace-2-hazard-prod-qgs/worker-test-input.qgs

$ gcloud pubsub subscriptions create temp-verify-sub-0021 --topic=grace-2-worker-events
Created subscription [projects/grace-2-hazard-prod/subscriptions/temp-verify-sub-0021].

$ gcloud run jobs execute grace-2-pyqgis-worker --project=grace-2-hazard-prod --region=us-central1 \
    --args="--qgs-uri,/mnt/qgs/worker-test-input.qgs,--layer-to-add,container-test-layer" --wait
Creating execution...
Provisioning resources...done
Starting execution...done
Running execution...done
Done.
Execution [grace-2-pyqgis-worker-2x7mc] has successfully completed.

$ gcloud run jobs executions describe grace-2-pyqgis-worker-2x7mc --region=us-central1 \
    --format="value(status.completionTime,status.succeededCount)"
2026-06-06T07:10:08.724360Z  1
```

### Execution logs
```
$ gcloud logging read 'resource.type=cloud_run_job AND ... execution_name="grace-2-pyqgis-worker-2x7mc"' ...
2026-06-06 07:10:00,850 INFO grace2.worker.pyqgis — read /mnt/qgs/worker-test-input.qgs — layers_before=['basemap-osm-conus']
2026-06-06 07:10:00,963 INFO grace2.worker.pyqgis — post-mutate layers_after=['basemap-osm-conus', 'container-test-layer']
{
  "qgs_uri": "/mnt/qgs/worker-test-input.qgs",
  "layers_before": [ "basemap-osm-conus" ],
  "layers_after": [ "basemap-osm-conus", "container-test-layer" ],
  "notify_message_id": "19943000011589039",
  "status": "ok"
}
Container called exit(0).
```

### Pub/Sub envelope (pulled then sub deleted)
```
$ gcloud pubsub subscriptions pull temp-verify-sub-0021 --auto-ack --limit=10 --format=json
[{"message":{"data":"<b64>","messageId":"19943000011589039","publishTime":"2026-06-06T07:10:05.403Z"}}]
$ echo "<b64>" | base64 -d
{"qgs_uri":"/mnt/qgs/worker-test-input.qgs",
 "layers_before":["basemap-osm-conus"],
 "layers_after":["basemap-osm-conus","container-test-layer"],
 "notify_message_id":null,"status":"ok","error":null,
 "qgs_version":"3.44.11-Solothurn","ts":"2026-06-06T07:10:01.726Z"}
$ gcloud pubsub subscriptions delete temp-verify-sub-0021 --project=grace-2-hazard-prod
Deleted subscription [projects/grace-2-hazard-prod/subscriptions/temp-verify-sub-0021].
```
notify_message_id is null in the published payload (chicken-and-egg per job-0020 OQ-20G); message.messageId carries the value externally.

### Mutation verification (downloaded .qgs)
```
$ gcloud storage cp gs://grace-2-hazard-prod-qgs/worker-test-input.qgs /tmp/post-worker.qgs
$ grep -c '<maplayer ' /tmp/post-worker.qgs
2
$ grep -oE '<layer-tree-layer [^>]*name="[^"]*"' /tmp/post-worker.qgs | sed 's/.*name="\([^"]*\)".*/\1/'
container-test-layer
basemap-osm-conus
```
Layer count 1→2; second layer matches --layer-to-add arg. (XML-grep substitutes for spinning up local grace2 QGIS Python — envelope + container log + .qgs XML are all consistent.)

### SA scoping (Invariants 5 + 6)
```
$ gcloud projects get-iam-policy grace-2-hazard-prod --format=json --flatten=bindings | jq ...
(empty — ZERO project-level bindings for the SA)

$ gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-qgs --format=json | jq ...
{ "role": "roles/storage.objectAdmin",
  "members": ["serviceAccount:pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com"] }

$ gcloud pubsub topics get-iam-policy grace-2-worker-events --project=grace-2-hazard-prod --format=json | jq ...
{ "role": "roles/pubsub.publisher",
  "members": ["serviceAccount:pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com"] }
```
Exactly two narrow bindings, both at resource scope. No project-wide grants.

### Tests run
- Local unit tests: none added (job-0020 owns services/workers/pyqgis/test_worker_local.py; frozen here).
- Live Cloud Run Job execution: 1 SEGV (diagnosed → QT_QPA_PLATFORM fix), then 1 PASS.
- tofu plan post-apply (worker scope): clean.

### Results
PASS. All acceptance criteria from the kickoff verified live against the deployed substrate:
- infra/worker/Dockerfile exists; Cloud Build succeeds; image in AR.
- linux/amd64-only (Cloud Build default builder).
- gcloud run jobs describe grace-2-pyqgis-worker returns 900s timeout (= 15m), pyqgis-worker-runtime SA, task_count=1, parallelism=1, max_retries=0.
- SA has ONLY two narrow bindings; ZERO project-level grants.
- Live execution returns exit 0; envelope shows layer added; Pub/Sub envelope pulled + validated; temp sub cleaned up.
- tofu plan (worker scope): No changes.

## Cross-cutting principles compliance

- Pre-MVP scope — no legacy support: no AWS ECR fallback, no arm64 matrix, no /vsigs/ vs /mnt/ "support both" branch.
- Remove don't shim: no commented-out alternative-base blocks in the Dockerfile; first build's SEGV diagnosis written into the report and the Dockerfile fix is the actual fix.
- Live E2E validation required: verbatim Cloud Build, execute, logging-read, sub-pull, IAM-policy transcripts above.
- Diagnose before fix: first-exec SEGV → named the failing layer (QGIS Qt init without QPA platform) before patching. Documented in Decisions + Dockerfile comments.
- Surface uncertainty: 6 TENTATIVE-tagged Open Questions above.
- Bundle small fixes; scan for all instances: scanned the Cloud Run gen2 mount pattern — QGIS Server (read-only) + worker (writable) both use volumes.gcs form; no third instance to sweep.
- Don't edit in-flight kickoffs: frozen, not edited.
