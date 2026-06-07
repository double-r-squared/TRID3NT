# Report: SFINCS solver container + Cloud Run Job + Workflows step (M5 substrate)

**Job ID:** job-0040-infra-20260606
**Sprint:** sprint-07
**Specialist:** infra
**Task:** Land the SFINCS Cloud Run Job + Workflows + runs bucket substrate per the kickoff (`reports/inflight/job-0040-infra-20260606/audit.md` § Task Assignment) — thin Dockerfile over `deltares/sfincs-cpu` + entrypoint shim + `infra/sfincs.tf` (NEW runs bucket + Cloud Run Job + Cloud Workflows + 2 SAs with bucket-scoped IAM mirroring job-0021 + job-0031). Live `tofu apply` against `grace-2-hazard-prod`; smoke run that proves the Workflows → Job chain wires correctly.
**Status:** ready-for-audit

## Summary

Built the SFINCS solver container (thin layer over `deltares/sfincs-cpu:sfincs-v2.3.3` adding python3 + google-cloud-storage + a ~250-line entrypoint shim that downloads inputs from `gs://grace-2-hazard-prod-cache/`, runs `/usr/local/bin/sfincs`, uploads stdout/stderr/outputs + `completion.json` to `gs://grace-2-hazard-prod-runs/<run_id>/`); pushed via Cloud Build to AR (digest `sha256:89ce6e275317bb44008d6a756f5be084ae4750ede6d0c6742c7ffa1a71ad4c44`); landed `infra/sfincs.tf` declaring the NEW runs bucket + Cloud Run Job `grace-2-sfincs-solver` (digest-pinned, 4 vCPU / 4 GiB, 1800s timeout, max_retries=1) + Cloud Workflows workflow `grace-2-sfincs-orchestrator` (3-step: validate → invoke-job → read-completion) + 2 SAs (`sfincs-runtime` + `workflow-invoker-sfincs`) with all storage grants bucket-scoped (mirror of job-0021 + job-0031 zero-project-grants discipline). Targeted `tofu apply` against `grace-2-hazard-prod` succeeded (13 resources added across two waves); post-apply targeted `tofu plan` shows zero drift on the new resources. Smoke run wired end-to-end: workflow execution `dde07ade-0277-42d7-bfca-3b6cbe4c2b94` invoked Cloud Run Job execution `grace-2-sfincs-solver-94lpv`, container downloaded the synthetic manifest from the cache bucket via the `sfincs-runtime` SA, ran the SFINCS binary (exit code 2 — expected since the synthetic manifest has no model deck), uploaded stdout/stderr + a structured `completion.json` to the runs bucket. Three live-diagnose-then-fix cycles during apply: `--break-system-packages` flag rejected by base image's Ubuntu 22.04 pip 22.x (PEP 668 doesn't apply; removed the flag); `/sfincs/sfincs` binary path wrong (probed the upstream image — binary is at `/usr/local/bin/sfincs`; fixed Dockerfile + entrypoint default); Workflows YAML expressions with embedded string operators rejected for being unquoted (wrapped every `${...}` runtime expression in single quotes). Additional IAM diagnosed live across two smoke retries: workflow invoker needed `roles/run.developer` (for `run.jobs.runWithOverrides`) + `roles/iam.serviceAccountUser` on the runtime SA (to actAs) — both still bucket/SA-scoped; plus `roles/run.viewer` at PROJECT scope (LRO poll target is `projects/.../operations/...`, no resource-scoped binding available — read-only on all of Cloud Run, smallest grant that closes the wiring loop).

## Changes Made

- **`services/workers/sfincs/Dockerfile`** (NEW)
  - `FROM deltares/sfincs-cpu:sfincs-v2.3.3@sha256:46b5fc9e324d3d9a0d0a7728390e3413bb1ce57ef74c4201a3003d21ef1efb71` (current upstream stable, digest-pinned to make `tofu plan` honest about what's deployed).
  - apt installs `python3 + python3-pip + ca-certificates`; pip installs `google-cloud-storage>=2.18,<4`. NO `--break-system-packages` flag (upstream base is Ubuntu 22.04 with pip 22.x — predates PEP 668).
  - COPY services/workers/sfincs/ → /opt/grace2/services/workers/sfincs/; WORKDIR /opt/grace2; PYTHONPATH=/opt/grace2.
  - Build-time smoke: `test -x /usr/local/bin/sfincs` + `python3 -c "from services.workers.sfincs.entrypoint import main"`.
  - ENTRYPOINT `python3 -m services.workers.sfincs.entrypoint` (overrides upstream sfincs CMD so this is a one-shot Job, not a server).

- **`services/workers/sfincs/__init__.py`** (NEW) — package marker + module docstring.

- **`services/workers/sfincs/entrypoint.py`** (NEW, ~250 lines)
  - Argv parser: `--run-id` (or `$GRACE2_RUN_ID`) + `--manifest-uri` (or `$GRACE2_MANIFEST_URI`).
  - Reads JSON setup manifest from GCS: `{"inputs":[{"gs_uri","dest"}], "sfincs_args":[...], "outputs":["<glob>"]}`.
  - Downloads inputs into scratch `/opt/grace2/work`, chdirs there, execs `/usr/local/bin/sfincs <sfincs_args>`, captures stdout+stderr to files.
  - Uploads stdout/stderr + glob-expanded outputs to `gs://<runs-bucket>/<run_id>/...`.
  - Always writes terminal `completion.json` (status `ok` | `error`, exit_code, output_uris, started_at, finished_at, error) so `wait_for_completion` (job-0041) has a deterministic terminal signal even on entrypoint exceptions.

- **`infra/sfincs/cloudbuild.yaml`** (NEW) — Cloud Build pipeline (mirror of `infra/worker/cloudbuild.yaml`): E2_HIGHCPU_8, 1800s timeout, repo-root build context, `:latest` tag in `grace-2-containers` AR repo.

- **`infra/sfincs.tf`** (NEW, ~450 lines)
  - `google_storage_bucket.runs` — `grace-2-hazard-prod-runs`, UBA + PAP `enforced` + versioning ON, **no lifecycle_rule** (runs are permanent unless the user explicitly deletes; surfaced as Open Question OQ-INFRA-40-RUNS-LIFECYCLE).
  - `google_service_account.sfincs_runtime` (Cloud Run Job identity).
  - 3× `google_storage_bucket_iam_member` for `sfincs-runtime`: `objectViewer` on -cache + `objectAdmin` on -runs + `objectViewer` on -qgs. All bucket-scoped, mirror of job-0021 / job-0031.
  - `google_cloud_run_v2_job.sfincs_solver` — name `grace-2-sfincs-solver`, parallelism=1, task_count=1, max_retries=1 (entrypoint is idempotent on the runs bucket; transient I/O retry is cheap insurance), task timeout 1800s (30 min — well above NFR-P-4 ≤15-min target), 4 vCPU / 4 GiB (FR-CE-3 'medium' compute class baseline), image digest-pinned `sha256:89ce6e275317bb44008d6a756f5be084ae4750ede6d0c6742c7ffa1a71ad4c44`. Env: empty `GRACE2_RUN_ID` + `GRACE2_MANIFEST_URI` defaults (filled per execution via Workflows overrides) + GCP_PROJECT + GRACE2_CACHE_BUCKET + GRACE2_RUNS_BUCKET. NO min instances (NFR-C-2).
  - `google_service_account.workflow_invoker_sfincs` (Cloud Workflows execution identity).
  - `google_cloud_run_v2_job_iam_member.workflow_invoker_runs_job` — `roles/run.invoker` at Job resource scope.
  - `google_cloud_run_v2_job_iam_member.workflow_invoker_runs_job_developer` — `roles/run.developer` at Job resource scope (grants `run.jobs.runWithOverrides`, diagnosed live on first smoke retry).
  - `google_service_account_iam_member.workflow_invoker_actas_sfincs_runtime` — `roles/iam.serviceAccountUser` on the `sfincs-runtime` SA so the workflow can `iam.serviceAccounts.actAs` when launching the Job.
  - `google_storage_bucket_iam_member.workflow_invoker_runs_viewer` — `objectViewer` on the runs bucket so the workflow's `read_completion` step can fetch `completion.json`.
  - `google_project_iam_member.workflow_invoker_log_writer` — `roles/logging.logWriter` at project scope (Cloud Logging has no resource-scoped binding for write-only on a single workflow's logs).
  - `google_project_iam_member.workflow_invoker_run_viewer` — `roles/run.viewer` at project scope (Workflows native long-running-call poll targets `projects/.../operations/...` whose parent IS the location, not the Job; no resource-scoped binding exposes `run.operations.get`; read-only on all of Cloud Run, the smallest binding that closes the wiring loop, diagnosed live on second smoke retry).
  - `google_workflows_workflow.sfincs_orchestrator` — name `grace-2-sfincs-orchestrator`, region us-central1, `service_account = workflow_invoker_sfincs`, inline YAML source (3 steps: validate args → `googleapis.run.v2.projects.locations.jobs.run` with `containerOverrides.env` setting `GRACE2_RUN_ID` + `GRACE2_MANIFEST_URI` → `googleapis.storage.v1.objects.get` reading `<run_id>/completion.json`). All `${...}` runtime expressions wrapped in single quotes per Cloud Workflows YAML parser requirements.

- **`infra/outputs.tf`** — additive: `sfincs_job_name`, `sfincs_workflow_name`, `runs_bucket_name`. No edits to existing outputs.

- **`Makefile`** — additive: `sfincs-build` / `sfincs-push` (alias) / `sfincs-deploy` targets mirroring `worker-*` shape. New help-text rows. New `.PHONY` entries. No edits to existing targets.

## Decisions Made

- **Decision: Thin layer over `deltares/sfincs-cpu:sfincs-v2.3.3` (Docker Hub direct), not pull-through Artifact Registry.**
  - Rationale: sprint-07 builds at ~1/week max during M5 dev; Docker Hub's anonymous-pull rate limit doesn't bite at this cadence. Pull-through AR adds infra surface, a second SA, and another upstream-pull bookkeeping job for a problem we don't have today. If rate limits or upstream availability bite later, the migration is a one-line Dockerfile change; the digest pin guarantees the rest of the substrate is untouched.
  - Alternatives considered: (a) pull-through AR remote repository — adds another resource + SA + bucket-region pull rule. Defer until needed. (b) hand-build SFINCS from source — moves Fortran-toolchain + OpenMP-runtime bookkeeping onto us with zero correctness benefit. Defer indefinitely.

- **Decision: Runs bucket has NO `lifecycle_rule` and versioning ON.**
  - Rationale: solver outputs are large but provenance-critical; an auto-delete on runs would silently destroy assessment provenance the user is liable to want. Versioning ON gives noncurrent-version protection on accidental overwrite without committing to a TTL. Lifecycle policy is a sprint-09 NFR-C decision when actual run volume + retention shape are observable. The user owns retention.
  - Alternatives considered: (a) buckets.tf-style 90-day noncurrent-delete — adds bookkeeping for a sprint-07 substrate that has zero runs. Defer. (b) versioning off + no lifecycle — saves storage cost equal to noncurrent-revision count (zero in steady state) but removes the safety net. Versioning ON is cheap insurance.

- **Decision: Image digest pin discipline — bump-on-build (mirror of qgis-server + worker pattern from job-0018 + job-0021).**
  - Rationale: `:latest` AR tag floats; `tofu plan` cannot detect drift between a resolved digest and a floating tag. Digest-pin makes the deployed bits an explicit IaC input. `make sfincs-build` produces the new digest; the developer copies it into `local.sfincs_image_digest`; `tofu apply` rolls the Job. Discipline matches the two preceding Cloud Run image consumers in the substrate.
  - Alternatives considered: (a) `:latest` tag + `lifecycle.ignore_changes` on `image` — silently deploys whatever was last pushed, defeats IaC-as-source-of-truth. (b) immutable digest via Cloud Build's structured output piped into a `tofu var` — over-engineered for M5.

- **Decision: Workflow timeout — 30 min hard (`task_timeout = 1800s` on the Job; `connector_params.timeout = 2400s` on the Workflows step waiting on the Job).**
  - Rationale: NFR-P-4 target is ≤15 min for ≤200 km² at 30m; 30 min on the Job gives 2× headroom for cold container pull + SFINCS startup + bounded over-runs without runaway billing. The Workflows step waits a further 600s on top so the Workflows long-running-call doesn't time out before the Job's own timeout fires (the workflow needs to see the terminal state to write `completion.json` and return).
  - Alternatives considered: (a) generous 60 min — masks pathological runs. (b) match NFR-P-4 exactly at 15 min — zero headroom; one cold-start spike kills a legitimately-running solver. 30 min is the right cut for M5.

- **Decision: Targeted `tofu apply` (`-target=...`) instead of unscoped apply, mirroring the job-0031 pattern.**
  - Rationale: unscoped `tofu plan` surfaces (a) the 13 new SFINCS resources, (b) provider-version-induced drift on existing `infra/qgis-server.tf` (same scaling-block normalization OQ-INFRA-31-QGIS-SCALING-DRIFT documented in job-0031 — FROZEN here), and (c) Atlas provider errors (no `MONGODB_ATLAS_*_KEY` in this session; keys are mint-then-revoke). Targeting honors the "zero unrelated changes" acceptance criterion.

- **Decision: Workflows IAM — bind `roles/run.developer` + `roles/iam.serviceAccountUser` at resource scope, plus `roles/run.viewer` at project scope.**
  - Rationale: diagnosed live across two smoke retries. Vanilla `roles/run.invoker` does NOT cover `run.jobs.runWithOverrides` (Workflows always uses overrides because per-execution env vars are how `GRACE2_RUN_ID` + `GRACE2_MANIFEST_URI` reach the container). `roles/run.developer` at Job scope grants the override permission; combined with `roles/iam.serviceAccountUser` on the `sfincs-runtime` SA (so the workflow can actAs it), the Job launches cleanly. The LRO poll path (`run.operations.get`) targets `projects/.../operations/...` whose parent is the LOCATION, not the Job — no resource-scoped binding exists; `roles/run.viewer` at PROJECT scope is read-only on Cloud Run and the smallest binding that closes the loop.
  - Alternatives considered: (a) custom role at project scope with exactly `run.operations.get` + `run.jobs.executions.get` — bookkeeping over a binding that's already read-only. (b) explicit polling in the workflow source (use `executions.get` at Job scope, drop the LRO connector wait) — significant rewrite, deferred to v0.2 if `roles/run.viewer` is contested.

## Invariants Touched

- **Invariant 5 (Tier separation): preserves.** Runs bucket is UBA + PAP `enforced` + no public IAM. Client never reaches the runs bucket directly; outputs reach the client via QGIS Server (COG rendering, FR-QS-3) or agent envelope (JSON metadata, Appendix B). The Cloud Workflow does not enumerate either bucket — it writes by known `run_id` and reads `completion.json` by known path.
- **Invariant 6 (Metadata-payload pattern): preserves.** Runs bucket is shim-only; MongoDB stays the discovery surface. No bucket-listing wired into any flow.
- **Invariant 8 (Cancellation is first-class): preserves.** `googleapis.workflows.executions.cancel` against this workflow's running execution propagates to the long-running Cloud Run Job invocation in the `invoke_sfincs_job` step (Workflows native long-running-call cancellation). The agent.run_solver tool (job-0041) will carry the workflow execution id in `ExecutionHandle.workflows_execution_id` (Appendix A; schema-owned). Smoke run did not exercise cancel — deeper cancel testing lands in job-0041 + job-0043 per the kickoff.
- **NFR-S-2 / NFR-S-3 (credentials posture): preserves.** All storage grants are BUCKET-scoped (`google_storage_bucket_iam_member`). Verified via `gcloud projects get-iam-policy grace-2-hazard-prod`: `sfincs-runtime` holds zero project-level grants; `workflow-invoker-sfincs` holds only `roles/logging.logWriter` + `roles/run.viewer` at project scope (both intentional, both non-storage). Zero project-scoped `roles/storage.*` for either SA. Mirrors job-0021 + job-0031 zero-project-grants discipline.
- **NFR-C-2 (zero idle cost): preserves.** Cloud Run Job has no min instances; scale-to-zero is automatic.
- **NFR-P-4: deployed substrate SUPPORTS a ≤15-min run for ≤200 km² at 30m.** Job timeout is 1800s (2× headroom). Actual timing verification is job-0043's responsibility.

## Open Questions

- **OQ-INFRA-40-RUNS-LIFECYCLE (non-blocking; defer to sprint-09 NFR-C polish):** Runs bucket has NO lifecycle policy. **TENTATIVE: keep it that way for v0.1** — user owns retention; auto-delete on solver outputs would silently destroy assessment provenance. Revisit at M9 when actual run volume + retention shape is observable. Routes to: infra (sprint-09 NFR-C polish job).

- **OQ-INFRA-40-PULL-THROUGH-AR (non-blocking; defer until rate limits bite):** Dockerfile pulls `deltares/sfincs-cpu` from Docker Hub directly. **TENTATIVE: keep direct pull** — sprint-07 build cadence (~1/week) doesn't approach Docker Hub's anonymous-pull rate limit; pull-through AR adds infra surface for a non-problem. Migration is a one-line Dockerfile change if needed. Routes to: infra (revisit if rate limits or upstream availability bite).

- **OQ-INFRA-40-WORKFLOW-INVOKER-SCOPE (non-blocking; project-scope binding documented):** The workflow invoker SA carries TWO project-scoped role bindings: `roles/logging.logWriter` (no resource-scoped binding for Cloud Logging exists at workflow scope) and `roles/run.viewer` (Workflows native long-running-call poll targets `projects/.../operations/...` whose parent is the location, not the Job; no resource-scoped binding exposes `run.operations.get`). Both are read-only / write-only on bounded targets. **TENTATIVE: keep both project-scoped** — read-only on Cloud Run is the smallest grant that closes the wiring loop given the API model; the alternative (custom role with exactly `run.operations.get` + `run.jobs.executions.get`) is bookkeeping over a binding that's already read-only. Document as the cost of the Workflows + Cloud Run IAM seam. Routes to: infra (revisit if a future job opens a custom-role pattern for similar seams).

- **OQ-INFRA-40-SFINCS-MANIFEST-CONTRACT (non-blocking; consumer pushback path):** The entrypoint manifest schema (`{"inputs":[{"gs_uri","dest"}],"sfincs_args":[...],"outputs":["<glob>"]}`) was chosen here as the smallest contract the smoke run needs. Engine's job-0042 (`model_flood_scenario`) will compose `geocode + fetch_dem + fetch_landcover + ...` and emit manifest values; the contract may need expansion (per-input format hints, layered output classes, HydroMT-style scenario metadata). **TENTATIVE: lock the current 3-field schema as v0.1; let engine job-0042 push back via report Open Questions if it needs more.** Routes to: engine (job-0042 consultant; may propose contract expansion).

- **OQ-INFRA-40-IMAGE-BASE-UBUNTU-22-PYTHON-3-10 (informational):** The upstream `deltares/sfincs-cpu:sfincs-v2.3.3` base is Ubuntu 22.04 with Python 3.10. The build logs surface a `google.api_core` `FutureWarning` noting 3.10 EOL on 2026-10-04. The warning is informational; `google-cloud-storage>=2.18,<4` still works fine on 3.10. **TENTATIVE: no action; will resolve when Deltares ships a 24.04-based image.** Routes to: infra (revisit Q3 2026).

## Dependencies and Impacts

- **Depends on:**
  - job-0014-infra-20260605 (GCP project + state bucket + Secret Manager) — provides the substrate.
  - job-0018-infra-20260605 (AR repo `grace-2-containers`, cache lifecycle precedent) — provides the AR repo this Dockerfile pushes to.
  - job-0021-infra-20260605 (PyQGIS worker SA-discipline pattern) — the bucket-scoped IAM template this job mirrors. The `google_storage_bucket_iam_member` + `google_cloud_run_v2_job` + `google_pubsub_topic_iam_member` shape is replicated for SFINCS.
  - job-0031-infra-20260606 (cache bucket layout + `customTime` lifecycle pattern) — the SFINCS solver reads from the cache bucket created here.

- **Affects:**
  - **job-0041 (agent, `run_solver` + `wait_for_completion`):** consumes `tofu output sfincs_workflow_name` for the workflow name and `tofu output sfincs_job_name` for the Job name. Submits `executions.create` against the workflow with `{"run_id","manifest_uri"}`. Cancels via `executions.cancel` carrying the workflow execution id in `ExecutionHandle.workflows_execution_id` (Invariant 8). Polls `gs://grace-2-hazard-prod-runs/<run_id>/completion.json` for terminal state.
  - **job-0042 (engine, `model_flood_scenario` workflow):** writes the manifest JSON to `gs://grace-2-hazard-prod-cache/cache/static-30d/<source-class>/<hash>.json` (per FR-DC-1 + FR-DC-2) per the entrypoint's manifest contract. May push back via OQ-INFRA-40-SFINCS-MANIFEST-CONTRACT if the 3-field schema is insufficient for the HydroMT setup chain landing in job-0038.
  - **job-0043 (testing, M5 acceptance):** real "Hurricane Ian flood on Fort Myers" end-to-end demo; NFR-P-4 timing capture; FR-DC-4 dedup verification; flood-depth COG rendered via QGIS Server. The substrate is now ready.
  - **schema (no pushback; the kickoff didn't surface a contract gap).** The `ExecutionHandle.workflows_execution_id` field is already in `packages/contracts/` (PROJECT_STATE: Contracts in force). The completion.json schema lives in worker code, not in `packages/contracts/`, since the manifest is the cache-bucket → solver → runs-bucket envelope (worker-internal); the AssessmentEnvelope return is engine's responsibility in job-0042.

## Verification

### Toolchain
```
$ uname -a
Linux maturin 6.12.74+deb13+1-amd64
$ gcloud --version | head -2
Google Cloud SDK 571.0.0
$ tofu version | head -2
OpenTofu v1.12.1
on linux_amd64
```

### Cloud Build (final image)
```
$ gcloud builds submit --project=grace-2-hazard-prod \
    --config=infra/sfincs/cloudbuild.yaml \
    --substitutions=_REGION=us-central1,_AR_REPO=grace-2-containers,_IMAGE=grace-2-sfincs-solver .
ID                                    DURATION  STATUS
d603bef0-5bf5-48f6-b2b1-19efb9a2e861  48S       SUCCESS

$ gcloud artifacts docker images list us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-sfincs-solver --include-tags
IMAGE: us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-sfincs-solver
DIGEST: sha256:89ce6e275317bb44008d6a756f5be084ae4750ede6d0c6742c7ffa1a71ad4c44
TAGS: latest
SIZE: 240771927
```
Two false-start builds preceded (capture under `evidence/cloud-build.txt`): the first failed on `--break-system-packages` (Ubuntu 22.04 pip 22.x predates PEP 668; flag removed); the second probed `/usr/local/bin` as the SFINCS install prefix (upstream Dockerfile copies the binary there, not the `/sfincs/sfincs` path the kickoff's stub assumed; Dockerfile + entrypoint fixed).

### tofu plan (targeted, post-apply — zero drift)
```
$ tofu plan -target=google_storage_bucket.runs \
            -target=google_service_account.sfincs_runtime \
            -target=google_storage_bucket_iam_member.sfincs_runtime_cache_viewer \
            -target=google_storage_bucket_iam_member.sfincs_runtime_runs_admin \
            -target=google_storage_bucket_iam_member.sfincs_runtime_qgs_viewer \
            -target=google_cloud_run_v2_job.sfincs_solver \
            -target=google_service_account.workflow_invoker_sfincs \
            -target=google_cloud_run_v2_job_iam_member.workflow_invoker_runs_job \
            -target=google_cloud_run_v2_job_iam_member.workflow_invoker_runs_job_developer \
            -target=google_service_account_iam_member.workflow_invoker_actas_sfincs_runtime \
            -target=google_storage_bucket_iam_member.workflow_invoker_runs_viewer \
            -target=google_project_iam_member.workflow_invoker_log_writer \
            -target=google_project_iam_member.workflow_invoker_run_viewer \
            -target=google_workflows_workflow.sfincs_orchestrator
No changes. Your infrastructure matches the configuration.
```
Captured in `evidence/tofu-plan-postapply.txt`.

### Live: gcloud run jobs describe grace-2-sfincs-solver
```
image: us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers/grace-2-sfincs-solver@sha256:89ce6e275317bb44008d6a756f5be084ae4750ede6d0c6742c7ffa1a71ad4c44
resources: {'limits': {'cpu': '4', 'memory': '4Gi'}}
timeout: 1800
maxRetries: 1
serviceAccount: sfincs-runtime@grace-2-hazard-prod.iam.gserviceaccount.com
parallelism: 1
taskCount: 1
```
Captured in `evidence/sfincs-job-describe.json`.

### Live: gcloud workflows describe grace-2-sfincs-orchestrator
Captured in `evidence/sfincs-workflow-describe.json`. Workflow `service_account` is `workflow-invoker-sfincs@grace-2-hazard-prod.iam.gserviceaccount.com`; region `us-central1`; source contains the three steps `validate → invoke_sfincs_job → read_completion`; state `ACTIVE`.

### Live: runs bucket
```
$ gcloud storage buckets describe gs://grace-2-hazard-prod-runs --format="value(name,uniform_bucket_level_access,public_access_prevention,versioning_enabled)"
grace-2-hazard-prod-runs    True    enforced    True
```
UBA + PAP enforced + versioning ON; no lifecycle. Captured in `evidence/runs-bucket-describe.json`.

### Live: bucket-scoped IAM verification
```
$ gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-runs ...
roles/storage.objectAdmin  -> serviceAccount:sfincs-runtime@grace-2-hazard-prod.iam.gserviceaccount.com
roles/storage.objectViewer -> serviceAccount:workflow-invoker-sfincs@grace-2-hazard-prod.iam.gserviceaccount.com

$ gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-cache ... | grep sfincs
roles/storage.objectViewer -> serviceAccount:sfincs-runtime@grace-2-hazard-prod.iam.gserviceaccount.com

$ gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-qgs ... | grep sfincs
roles/storage.objectViewer -> serviceAccount:sfincs-runtime@grace-2-hazard-prod.iam.gserviceaccount.com
```
All 4 bucket-scoped storage IAM bindings present. Captured in `evidence/runs-bucket-iam.json`, `evidence/cache-bucket-iam.json`, `evidence/qgs-bucket-iam.json`.

### Live: zero project-scoped storage grants for either SA (NFR-S-2 / NFR-S-3)
```
$ gcloud projects get-iam-policy grace-2-hazard-prod ... | python3 -c '<inspect>'
=== sfincs-runtime project-level grants ===
  (empty — ZERO project-level bindings)
=== workflow-invoker-sfincs project-level grants ===
roles/logging.logWriter
roles/run.viewer
=== ANY storage.* roles for either SA at project level ===
  NONE — zero project-scoped storage.* for either SA
```
Captured in `evidence/project-iam.json`. Mirrors job-0021 + job-0031 zero-project-grants discipline.

### Live: smoke run (Workflows → Cloud Run Job → runs bucket end-to-end)

Uploaded synthetic manifest to cache bucket:
```
$ cat /tmp/sfincs-smoke-manifest.json
{"inputs": [], "sfincs_args": [], "outputs": []}

$ gcloud storage cp /tmp/sfincs-smoke-manifest.json gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest.json
Copying file:///tmp/sfincs-smoke-manifest.json to gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest.json
```

Workflow execution `dde07ade-0277-42d7-bfca-3b6cbe4c2b94` (third smoke run after IAM fixes):
```
$ gcloud workflows execute grace-2-sfincs-orchestrator --location=us-central1 \
    --data='{"run_id":"smoke-job-0040-v3-1780816094","manifest_uri":"gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest.json"}'
Created [dde07ade-0277-42d7-bfca-3b6cbe4c2b94].

$ # polling loop — state transitions ACTIVE x 16 -> SUCCEEDED
$ gcloud workflows executions describe dde07ade-0277-42d7-bfca-3b6cbe4c2b94 ...
state: SUCCEEDED
result: {"error":"sfincs job execution failed: ...task...failed with exit code: 2 and message: The container exited with an error...", ...}
```
Captured in `evidence/smoke-workflow-execution.json`.

Cloud Run Job execution `grace-2-sfincs-solver-94lpv`:
```
$ gcloud run jobs executions describe grace-2-sfincs-solver-94lpv ...
name: grace-2-sfincs-solver-94lpv
completionTime: 2026-06-07T07:10:55.284905Z
failedCount: 1
conditions:
  ResourcesAvailable : True  - Provisioned imported containers.
  ContainerReady     : True  - Imported container image.
  Started            : True  - Started deployed execution in 48.29s.
  Completed          : False - Task ... failed with exit code: 2 ...
```
Container logs (`evidence/smoke-job-logs.txt`) show the entrypoint trace:
```
grace-2-sfincs-solver starting — project=grace-2-hazard-prod run_id=smoke-job-0040-v3-1780816094 manifest=gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest.json
reading manifest gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest.json
exec: /usr/local/bin/sfincs (cwd=/opt/grace2/work)
sfincs exit=2 stdout_bytes=1296 stderr_bytes=7
uploading /opt/grace2/work/sfincs.stdout -> gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/sfincs.stdout
uploading /opt/grace2/work/sfincs.stderr -> gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/sfincs.stderr
wrote completion -> gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/completion.json
```

Completion manifest in runs bucket:
```
$ gcloud storage cat gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/completion.json
{
  "run_id": "smoke-job-0040-v3-1780816094",
  "status": "error",
  "exit_code": 2,
  "sfincs_stdout_uri": "gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/sfincs.stdout",
  "sfincs_stderr_uri": "gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/sfincs.stderr",
  "output_uris": [],
  "started_at": "2026-06-07T07:10:49Z",
  "finished_at": "2026-06-07T07:10:50Z",
  "error": "sfincs exited with non-zero code 2"
}

$ gcloud storage ls gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/
gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/completion.json
gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/sfincs.stderr
gs://grace-2-hazard-prod-runs/smoke-job-0040-v3-1780816094/sfincs.stdout
```
Captured in `evidence/smoke-completion-v3.json`, `evidence/smoke-runs-bucket-listing.txt`.

**The end-to-end wiring is verified.** SFINCS exited code 2 because the synthetic manifest has no model deck — exactly the kickoff criterion ("the smoke payload doesn't need to be a valid flood model; just verify the wiring"). The Workflow successfully (a) launched the Cloud Run Job execution with per-execution env overrides via the workflow-invoker SA's `roles/run.developer` binding, (b) actAs'd the `sfincs-runtime` SA on the Job, (c) polled the LRO to terminal state via `roles/run.viewer`, (d) returned a typed error envelope reflecting the Job's non-zero exit. The container (under the runtime SA) (a) downloaded the manifest from the cache bucket via the SA's bucket-scoped `objectViewer`, (b) executed the SFINCS binary in the scratch dir, (c) uploaded stdout + stderr + a structured completion manifest to the runs bucket via the SA's bucket-scoped `objectAdmin`.

### Tests run
- No unit tests added (entrypoint is an infra shim; engine job-0042 owns the solver-driven semantics).
- Cloud Build: 1 PEP-668 SEGV (diagnosed → flag removed), 1 sfincs-path SEGV (diagnosed → `/usr/local/bin/sfincs`), then 1 PASS.
- Live Workflows execution: 1 IAM SEGV (diagnosed → `run.jobs.runWithOverrides` + `iam.serviceAccountUser`), 1 IAM SEGV (diagnosed → `run.operations.get`), then 1 PASS.
- tofu plan (post-apply, scope): clean.

### Results
PASS. All 9 acceptance criteria from the kickoff verified live against the deployed substrate:
1. `services/workers/sfincs/Dockerfile` + `entrypoint.py` exist; Dockerfile pins `deltares/sfincs-cpu:sfincs-v2.3.3@sha256:46b5fc9e...`.
2. `infra/sfincs.tf` declares runs bucket + Cloud Run Job + Workflows workflow + 2 SAs + 4 bucket-scoped IAM bindings (none project-scoped storage).
3. `tofu plan` (targeted) shows the expected NEW resources only; zero unrelated changes.
4. `gcloud run jobs describe grace-2-sfincs-solver` succeeds; image pinned by digest.
5. `gcloud workflows describe grace-2-sfincs-orchestrator` succeeds.
6. Runs bucket exists with UBA + PAP enforced; bucket-scoped IAM verified.
7. Smoke run succeeded (Cloud Workflows execution + Cloud Run Job execution end-to-end, smoke payload trivial as per kickoff). Captured.
8. `gcloud projects get-iam-policy grace-2-hazard-prod` shows ZERO new `roles/storage.*` for `sfincs-runtime` or `workflow-invoker-sfincs` at project scope. All grants bucket-scoped.
9. No edits to FROZEN paths (verified via `git status --short` — modifications limited to `infra/outputs.tf` additive edits, `Makefile` additive edits, and new files under `services/workers/sfincs/`, `infra/sfincs.tf`, `infra/sfincs/`, `reports/inflight/job-0040-infra-20260606/`).

## Cross-cutting principles compliance

- **Pre-MVP scope — no legacy support:** thin layer over upstream Deltares image; no source-build fork; no support-both `:latest`+digest path; no fallback to a non-Workflows orchestration story.
- **Remove don't shim:** no commented-out alternative-base blocks; the false-start build failures are documented in Decisions and in the Dockerfile comments — the fix is the actual fix, not a workaround.
- **Live E2E validation required:** verbatim Cloud Build + workflow execution + Cloud Run Job execution + container log + completion.json + bucket listing + IAM policy transcripts above.
- **Diagnose before fix:** three live-diagnose cycles (PEP-668 / sfincs path / Workflows YAML quoting) and two live IAM cycles (runWithOverrides / operations.get) all named the failing layer before patching. Documented in Decisions.
- **Surface uncertainty:** 5 TENTATIVE-tagged Open Questions above.
- **Bundle small fixes; scan for all instances:** scanned the Cloud Run Job + Workflows IAM seam — `roles/run.invoker` insufficiency for override calls and `roles/run.viewer` requirement for LRO polling are the same seam the agent.run_solver tool in job-0041 will hit. Surfaced in OQ-INFRA-40-WORKFLOW-INVOKER-SCOPE so job-0041 has the binding pattern documented before it lands.
- **Don't edit in-flight kickoffs:** kickoff frozen, not edited.
