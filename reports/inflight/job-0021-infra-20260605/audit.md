# Audit: PyQGIS worker container Dockerfile + image build/push + Cloud Run Job deployment

**Job ID:** job-0021-infra-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** infra
**Prerequisites:** job-0018-infra-20260605 (must be `approved`) — provides Artifact Registry, the GCS buckets, Pub/Sub topic `grace-2-worker-events`, and the pinned `qgis/qgis-server` base image digest. job-0020-engine-20260605 (must be `approved`) — provides `services/workers/pyqgis/worker.py` + `types.py` + `__init__.py` (the code to package); the CLI entrypoint signature (`python -m services.workers.pyqgis.worker --qgs-uri <uri> --layer-name <name>`); the Pub/Sub envelope shape; the worker's dependency surface (`google-cloud-storage`, `google-cloud-pubsub`, and PyQGIS bindings from the base image).
**SRS references:** FR-CE-1 (containerization-as-Cloud-Run-Jobs pattern — the worker is the canonical first instance, reused for SFINCS at M5); FR-CE-2 (Cloud Workflows orchestration substrate readiness for M5 — no workflow definition lands in M2, deferred per OQ); FR-QS-6 (PyQGIS worker pattern — this job ships the runtime that executes the function); NFR-C-2 (Cloud Run Jobs scale to zero — `--parallelism=1 --max-retries=0` baseline); NFR-S-2 (service-account-scoped credentials via Workload Identity, no embedded creds in image); NFR-S-5 (no public access; worker SA scoped to `grace-2-hazard-prod-qgs` bucket + `grace-2-worker-events` topic ONLY); NFR-PO-3 (IaC — Cloud Run Job + IAM in OpenTofu); Decision C (PyQGIS workers — first deployed worker); Decision E (Google Cloud throughout); Invariant 4 (Rendering through QGIS Server — the Job is the only path that writes `.qgs`); Invariant 6 (Metadata-payload pattern — bucket non-public, non-enumerable).

### Environment

Dev + prod substrate Linux (Debian 13, `Linux maturin 6.12.74+deb13+1-amd64`, x86_64). Container builds `linux/amd64`-only (`docker buildx build --platform=linux/amd64`). Consume live cloud substrate from `PROJECT_STATE.md` + the upstream job audits: GCP project `grace-2-hazard-prod` (425352658356), Artifact Registry, three GCS buckets, Pub/Sub topic. Worker code at `services/workers/pyqgis/` (frozen per job-0020). Toolchain (`gcloud`, `docker 29.3.1`, `tofu`) already installed on this Debian 13 box. `python3-venv` unavailable — use `virtualenv` if needed for any helper.

### Scope

1. **Author `infra/worker/Dockerfile`**:
   - Base image: extend the SAME `qgis/qgis-server` digest job-0018 pinned (so worker QGIS version == server QGIS version — eliminates a project-version drift class). TENTATIVE base-image choice (surface as OQ): qgis/qgis-server extended vs from-scratch `python:3.12` + conda-forge PyQGIS vs separate qgis/qgis-server-python tag if available. Pick qgis/qgis-server extension.
   - Install `python3 python3-pip` if not present in base.
   - `pip install google-cloud-storage google-cloud-pubsub pydantic` (or `pydantic-core` — match what job-0020 chose for `WorkerResult`). Pin versions; surface drift policy as OQ.
   - `COPY services/workers/pyqgis/ /opt/grace2/services/workers/pyqgis/`.
   - `COPY styles/ /opt/grace2/styles/` (worker reads the same QML presets the QGIS Server image bakes — single source of truth from `styles/`).
   - `WORKDIR /opt/grace2`.
   - `ENTRYPOINT ["python", "-m", "services.workers.pyqgis.worker"]` — args (`--qgs-uri`, `--layer-name`) pass through as Cloud Run Job task args.
   - **Verify no embedded credentials.** No `.env`, no service account JSON, no Mongo SRV in the image. `docker history` review.
2. **Build + push image.**
   - Tag scheme: `us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-images/pyqgis-worker:m2-<short-sha>` plus `:latest` for M2 (surface tag-policy as OQ — sprint-04 acceptable to ship `:latest`; M3+ adopts digest-pin policy parallel to QGIS Server).
   - `docker buildx build --platform=linux/amd64 -t <tag> .` from repo root.
   - `docker push <tag>` to Artifact Registry.
3. **Cloud Run Job IaC** in `infra/worker/` (or extend `infra/gcp.tf` — surface placement choice):
   - `infra/worker/cloudrun.tf` declares `google_cloud_run_v2_job.pyqgis_worker`:
     - Name `grace-2-pyqgis-worker`, region `us-central1`.
     - Container image = the tag above.
     - `template.template.timeout = "15m"`, `task_count = 1`, `parallelism = 1`, `max_retries = 0` (NFR-C-2 scale-to-zero baseline; M2 is a smoke pattern, not a production sweep).
     - Service account `grace-2-pyqgis-worker` with `roles/storage.objectAdmin` on `grace-2-hazard-prod-qgs` bucket ONLY (NOT project-wide) and `roles/pubsub.publisher` on `grace-2-worker-events` topic ONLY (Workload Identity bound — no key downloads). If job-0018 already declared the SA bindings stub, populate the SA here; otherwise declare both here (cross-check with 0018 audit).
     - `env { name = "QGS_BUCKET"; value = "grace-2-hazard-prod-qgs" }`, `env { name = "PUBSUB_TOPIC"; value = "grace-2-worker-events" }`, `env { name = "GOOGLE_CLOUD_PROJECT"; value = "grace-2-hazard-prod" }`.
     - Args parameterized via `gcloud run jobs execute --args` at invocation time (`--qgs-uri gs://... --layer-name <name>`); do not bake test args into the Job spec.
   - `tofu plan` after apply must show **No changes**.
4. **Makefile targets at repo root** (additive — do not modify existing targets):
   - `make worker-build` — `docker buildx build --platform=linux/amd64 -t <tag> -f infra/worker/Dockerfile .`
   - `make worker-push` — `docker push <tag>`
   - `make worker-run-job QGS_URI=gs://... LAYER=<name>` — `gcloud run jobs execute grace-2-pyqgis-worker --region=us-central1 --args="--qgs-uri,$(QGS_URI),--layer-name,$(LAYER)" --wait`
5. **Live execution verification.** Execute the Job against the canonical sample `.qgs`: `make worker-run-job QGS_URI=gs://grace-2-hazard-prod-qgs/grace2-sample.qgs LAYER=cloud-demo`. Verify:
   - `gcloud run jobs executions describe <exec-id> --region=us-central1 --format='value(status.completionTime,status.succeededCount)'` shows succeeded.
   - Execution logs (`gcloud run jobs executions logs read <exec-id>`) show: `/vsigs/` read succeeded, mutation logged, `/vsigs/` write succeeded, Pub/Sub publish returned a `messageIds: [...]` value, `WorkerResult(status="ok")` logged.
   - `gcloud pubsub subscriptions create temp-verify-sub --topic=grace-2-worker-events && gcloud pubsub subscriptions pull temp-verify-sub --auto-ack --limit=10 --format=json` returns the completion envelope; cleanup the temp sub.
   - Verbatim transcripts in report.
6. **Budget itemization update.** Append to `infra/README.md`: Cloud Run Job at min=0 contributes $0/mo idle (executions billed per-second only); confirm total still < $100/mo (NFR-C-1).
7. **Open Questions to surface (TENTATIVE-tagged):**
   - Base image: qgis/qgis-server extension vs `python:3.12` + conda-forge PyQGIS. TENTATIVE: extend qgis/qgis-server (matches server version).
   - Tag policy: `:latest` for M2 vs digest-pin. TENTATIVE: `:m2-<sha>` + `:latest` dual-tag now; M3 adopts digest-pin uniformly.
   - Cloud Run Job vs Cloud Run service for the worker: Job is correct (FR-CE-1 + NFR-C-2). State decision; no alternative entertained.
   - Whether to wire a Cloud Workflows definition stub for future M5 use (FR-CE-2). TENTATIVE: defer to M5 (matches 0018 deferral).

### File ownership (exclusive)

**Parallel-ownership notes** (clarifies overlap with sibling infra jobs in this sprint):
- Root `Makefile` additions are additive only — this job adds `worker-build`, `worker-push`, `worker-deploy`, `worker-run-job` targets and does NOT edit `qgis-server-*` (owned by job-0018) or any prior target.
- New OpenTofu variables specific to the worker go in `infra/worker/variables.tf` (this job creates it), NOT in `infra/variables.tf` (owned by job-0018).
- `infra/README.md` additions are additive only — append a new "Worker" subsection; do NOT edit job-0018's QGIS Server prose.

- `infra/worker/Dockerfile`
- `infra/worker/*.tf` (or addition to existing `infra/gcp.tf` — declare placement)
- `infra/README.md` (additive)
- Root `Makefile` (add `worker-build`, `worker-push`, `worker-run-job` targets — do NOT modify existing targets or 0018's `qgis-server-*` targets)
- `infra/variables.tf` (additive — new vars only)

**FROZEN (do NOT edit):** `services/workers/pyqgis/**` (job-0020's deliverable, frozen — only `COPY` into the image), `styles/**` (job-0019's deliverable, frozen — only `COPY` into the image), `packages/contracts/**`, `services/agent/**`, `web/**`, `tests/**`, `docs/SRS_v0.3.md`, `public_hazard_catalog.yaml`. Existing `infra/qgis-server/**` (job-0018's deliverable) — frozen.

### Cross-cutting principles in force
*Bundle small fixes; scan for all instances* — when this job touches a known class of issue (e.g., a missing label on a labeled resource), sweep the whole sprint scope for similar instances and surface in the report.

Cite by name from AGENTS.md § "Cross-cutting principles":
- **Pre-MVP scope — no legacy support.** No `--platform=linux/arm64` matrix entry. No AWS ECR fallback paths.
- **Remove don't shim.** No commented-out `# Alternative: python:3.12 base` blocks in the Dockerfile.
- **Live E2E validation required.** Verbatim `make worker-build && make worker-push && make worker-run-job ...` transcripts; `gcloud run jobs executions describe` showing success; execution logs showing the round-trip; Pub/Sub pull showing the completion message; `docker manifest inspect` showing `linux/amd64` only; `docker history` review showing no embedded creds.
- **Diagnose before fix.** Build/deploy failures: name the failing layer (Dockerfile vs base-image cache vs IAM vs Workload Identity vs Pub/Sub topic vs `/vsigs/` GDAL config).
- **Surface uncertainty.** Every contestable choice → Open Question with TENTATIVE tag.
- **Don't edit in-flight kickoffs.** Frozen.
- **Infra: Terraform/OpenTofu from job one.** No console-clicked resources. Every resource in `infra/`.
- **Infra: Nothing writes `.qgs` except a PyQGIS worker.** This Job IS the writer. No other Cloud Run service in `infra/` has `roles/storage.objectAdmin` on the `-qgs` bucket.
- **Infra: Scale to zero by default.** Job has no min-instances concept (Jobs are inherently scale-to-zero); confirm `parallelism=1`, `max_retries=0`.
- **Infra: Secrets never land in code, repo, or images.** No SRV strings, no SA JSON keys in the image or `.tfvars` (use Workload Identity).
- **Infra: Every GCP resource is tagged/labeled.** `labels = { project = "grace-2", env = "dev", sprint = "04", component = "pyqgis-worker" }`.

### Acceptance criteria (reviewer re-runs)

- `infra/worker/Dockerfile` exists; `docker buildx build --platform=linux/amd64 -t test-tag -f infra/worker/Dockerfile .` succeeds locally.
- `docker manifest inspect <pushed-tag>` shows `architecture: amd64` only.
- `docker history <pushed-tag>` review (recorded transcript) shows no `COPY` of `.env`, `*.json` credential files, or `.tfvars`.
- `gcloud run jobs describe grace-2-pyqgis-worker --region=us-central1 --format='value(template.template.timeout,template.template.serviceAccount,template.taskCount,template.parallelism)'` returns `15m`, `grace-2-pyqgis-worker@grace-2-hazard-prod.iam.gserviceaccount.com`, `1`, `1`.
- `gcloud projects get-iam-policy grace-2-hazard-prod --format=json | jq '.bindings[] | select(.members[]=="serviceAccount:grace-2-pyqgis-worker@...")'` returns only the two scoped role bindings (`storage.objectAdmin` on `-qgs` bucket conditional, `pubsub.publisher` on the topic conditional) — NO `storage.admin`, NO project-wide grants.
- `make worker-run-job QGS_URI=gs://grace-2-hazard-prod-qgs/grace2-sample.qgs LAYER=cloud-demo` returns exit 0; execution succeeds; logs show all six FR-QS-6 steps; Pub/Sub message pull shows the completion envelope.
- `tofu plan` from `infra/` returns **No changes**.
- `infra/README.md` budget itemization updated; total < $100/mo.
- All Open Questions surfaced with TENTATIVE tags + SRS references.

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
