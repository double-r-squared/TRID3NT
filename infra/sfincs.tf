# sfincs.tf — SFINCS solver Cloud Run Job + Cloud Workflows + runs bucket.
#
# Sprint-07 / M5 / job-0040. The M5 substrate everything else in sprint-07
# leans on: a containerized SFINCS solver (services/workers/sfincs/) running
# as a Cloud Run Job named `grace-2-sfincs-solver`, orchestrated by Cloud
# Workflows `grace-2-sfincs-orchestrator` which the agent service submits
# `executions.create` against. Outputs land in `gs://grace-2-hazard-prod-runs/`
# — a NEW bucket separate from the cache bucket (cache holds atomic-tool
# fetches per FR-DC-1; runs hold persisted solver outputs per FR-CE-3).
#
# Provisions:
#   1. `gs://grace-2-hazard-prod-runs/` — UBA + PAP enforced + NO lifecycle
#      (runs are permanent unless the user explicitly deletes; lifecycle
#      policy is a sprint-09+ decision per OQ-INFRA-40-RUNS-LIFECYCLE).
#   2. `sfincs-runtime` service account (Cloud Run Job identity).
#   3. Bucket-scoped IAM mirroring job-0021 + job-0031:
#        - sfincs-runtime gets `objectViewer` on -cache (read inputs)
#        - sfincs-runtime gets `objectAdmin` on -runs (write outputs)
#        - sfincs-runtime gets `objectViewer` on -qgs (read .qgs setup files
#          when the agent stages a model deck in the canonical bucket)
#      ZERO project-scoped storage grants.
#   4. `grace-2-sfincs-solver` Cloud Run v2 Job — image pinned by digest,
#      4 vCPU / 4 GiB, max_retries=1, task_timeout=1800s (30 min).
#   5. `workflow-invoker-sfincs` service account — minimum permissions to
#      invoke the Cloud Run Job from the Cloud Workflow.
#   6. `grace-2-sfincs-orchestrator` Cloud Workflows workflow — 3 logical
#      steps (prepare-manifest -> invoke-cloud-run-job -> wait-and-collect).
#
# Image source-of-truth (mirror of job-0018 r1 / job-0021 discipline):
#   the Cloud Run Job pins the container image BY DIGEST below, not by the
#   `:latest` tag, so `tofu plan` detects when a newer image has been pushed
#   without an IaC change.
#
#   Bump-on-build workflow:
#     1. `make sfincs-build` (Cloud Build push) emits the new digest at AR.
#        Read it via:
#          gcloud artifacts docker images list \
#            us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers \
#            --include-tags | grep sfincs-solver
#     2. Update the digest on the `image = ...` line below.
#     3. `tofu apply` rolls the Job to the new image.
#     4. `tofu plan` after must return "No changes".
#
# Invariant compliance:
#   - Invariant 5 (Tier separation): runs bucket is UBA + PAP enforced + no
#     public IAM. Client never reaches the runs bucket directly; outputs
#     reach the client via QGIS Server (COG rendering) or agent envelope
#     (JSON metadata).
#   - Invariant 6 (Metadata-payload pattern): runs bucket is shim-only;
#     MongoDB is the discovery surface. The Cloud Workflow does not
#     enumerate the runs bucket — it writes by known run_id and reads
#     completion.json by known path.
#   - Invariant 8 (Cancellation is first-class): Cloud Workflows
#     `executions.cancel` propagates to the running Cloud Run Job execution.
#     The agent's run_solver (job-0041) carries the workflow execution id
#     in ExecutionHandle.workflows_execution_id (Appendix A; schema-owned).
#   - NFR-S-2 / NFR-S-3 (credentials posture): SA grants are BUCKET-SCOPED
#     (mirror of job-0021 / job-0031 zero-project-grants discipline).
#   - NFR-C-2 (scale-to-zero): Cloud Run Jobs are inherently scale-to-zero;
#     no min instances configured.
#   - NFR-P-4: the deployed substrate supports a <=15-min run for <=200 km²
#     at 30m; actual timing is verified in job-0043 (M5 acceptance).
#
# Labels (NFR-C-1 idle-cost breakdown): sprint=07 + component=sfincs-solver.

locals {
  sfincs_labels = merge(local.common_labels, {
    component = "sfincs-solver"
    sprint    = "07"
  })

  # Pinned image digest (bump-on-build per the workflow above).
  # 2026-06-06 — first successful build of services/workers/sfincs/Dockerfile
  # (FROM deltares/sfincs-cpu:sfincs-v2.3.3@sha256:46b5fc9e... + python3 +
  # google-cloud-storage + entrypoint shim). Cloud Build
  # d603bef0-5bf5-48f6-b2b1-19efb9a2e861. Two false-start builds preceded:
  # the first (--break-system-packages flag) failed because the base image's
  # Ubuntu 22.04 pip 22.x predates PEP 668; the second probed /usr/local/bin
  # as the SFINCS install prefix (the upstream Dockerfile copies the binary
  # there, not the /sfincs/sfincs path the kickoff's stub assumed). Both
  # fixes landed in this Dockerfile + entrypoint before this digest.
  # Replace digest after `make sfincs-build` resolves a new one and
  # `tofu apply` rolls the Job.
  sfincs_image_digest = "sha256:89ce6e275317bb44008d6a756f5be084ae4750ede6d0c6742c7ffa1a71ad4c44"
}

# --- Runs bucket ----------------------------------------------------------
#
# `gs://grace-2-hazard-prod-runs/` holds persisted solver outputs per
# FR-CE-3 / FR-MP-3. Each Cloud Run Job execution writes under
# `<run_id>/` — flood-depth COGs, water-level netCDF, the completion
# manifest. The cache bucket (job-0031) holds atomic-tool fetches under a
# different lifecycle posture; runs are permanent until the user retains
# them.
#
# Lifecycle policy: NONE in v0.1. The user owns retention; auto-delete on
# solver outputs would silently destroy assessment provenance. The
# alternative (90-day noncurrent-version delete on a versioned bucket)
# mirrors buckets.tf but adds bookkeeping for no v0.1 benefit. Surfaced
# as Open Question OQ-INFRA-40-RUNS-LIFECYCLE — TENTATIVE: defer to
# sprint-09 NFR-C polish.

resource "google_storage_bucket" "runs" {
  project  = google_project.grace2.project_id
  name     = "${google_project.grace2.project_id}-runs"
  location = var.gcp_region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Versioning ON: solver outputs are large but provenance-critical; a
  # noncurrent version on an accidental overwrite is cheap insurance even
  # without an explicit lifecycle policy. Matches the buckets.tf posture
  # for canonical payload buckets (-qgs / -cog / -fgb).
  versioning {
    enabled = true
  }

  # NO lifecycle_rule blocks — see comment above. Surfaced as Open Question.

  labels = local.sfincs_labels

  depends_on = [google_project_service.enabled]
}

# --- Service account: sfincs-runtime --------------------------------------
#
# Dedicated runtime SA for the SFINCS Cloud Run Job. The SA exists ONLY for
# this Job; no other resource binds to it. No keys are minted (Cloud Run
# attaches the runtime identity via the metadata server; no JSON keys).

resource "google_service_account" "sfincs_runtime" {
  project      = google_project.grace2.project_id
  account_id   = "sfincs-runtime"
  display_name = "GRACE-2 SFINCS solver Cloud Run Job runtime"
  description  = "Cloud Run Job identity for the SFINCS solver. objectViewer on -cache + -qgs; objectAdmin on -runs. No project-wide roles."

  depends_on = [google_project_service.enabled]
}

# --- IAM: bucket-scoped objectViewer on -cache (read inputs) -------------
#
# Mirror of job-0031 SA-discipline pattern. `objectViewer` grants
# storage.objects.get + .list on THIS bucket only; the SA cannot enumerate
# or read any other bucket. The SFINCS entrypoint downloads input files
# from the cache bucket per the manifest's `inputs[].gs_uri`.

resource "google_storage_bucket_iam_member" "sfincs_runtime_cache_viewer" {
  bucket = google_storage_bucket.cache.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.sfincs_runtime.email}"
}

# --- IAM: bucket-scoped objectAdmin on -runs (write outputs) -------------
#
# The SFINCS solver IS the only sanctioned writer of `gs://<...>-runs/`;
# `objectAdmin` at bucket scope authorizes the per-execution writes. No
# other resource holds a writer grant on this bucket — agent-runtime and
# pyqgis-worker-runtime do not write here.

resource "google_storage_bucket_iam_member" "sfincs_runtime_runs_admin" {
  bucket = google_storage_bucket.runs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.sfincs_runtime.email}"
}

# --- IAM: bucket-scoped objectViewer on -qgs (read .qgs setup files) -----
#
# Some SFINCS model decks stage their setup inputs through the canonical
# .qgs bucket (the agent's model_flood_scenario workflow in job-0042 may
# write a project file to gs://<...>-qgs/ then point the solver at it).
# objectViewer at bucket scope authorizes read access only. The solver
# does not WRITE .qgs files — that's the PyQGIS worker's surface
# (Invariant 4 / job-0021).

resource "google_storage_bucket_iam_member" "sfincs_runtime_qgs_viewer" {
  bucket = google_storage_bucket.qgs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.sfincs_runtime.email}"
}

# --- Cloud Run v2 Job: grace-2-sfincs-solver -----------------------------

resource "google_cloud_run_v2_job" "sfincs_solver" {
  project  = google_project.grace2.project_id
  name     = "grace-2-sfincs-solver"
  location = var.gcp_region

  labels = local.sfincs_labels

  template {
    labels = local.sfincs_labels

    # M5 smoke-pattern sizing: parallelism=1, task_count=1, max_retries=1.
    # max_retries=1 (not 0 as in job-0021) — SFINCS occasionally exits
    # cleanly on transient I/O errors at the storage layer that ARE worth
    # a single retry; the entrypoint is idempotent on the runs bucket so
    # a retry overwrites partial outputs cleanly. NFR-C-2 (scale-to-zero)
    # is automatic for Cloud Run Jobs.
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.sfincs_runtime.email

      # 30-minute task timeout — well above the NFR-P-4 <=15-min target
      # for <=200 km² at 30m, leaves headroom for cold container pull +
      # SFINCS startup + bounded over-runs without runaway billing.
      # M9+ regional runs that need more time will route through a
      # different (or templated) Job spec.
      timeout     = "1800s"
      max_retries = 1

      containers {
        # Digest-pinned (mirror of job-0021 discipline). Bump per the
        # workflow in the file header when `make sfincs-build` produces
        # a new digest. `:latest` AR tag points to the same digest.
        image = "${var.gcp_region}-docker.pkg.dev/${google_project.grace2.project_id}/${google_artifact_registry_repository.containers.repository_id}/grace-2-sfincs-solver@${local.sfincs_image_digest}"

        # FR-CE-3 'medium' compute class baseline (4 vCPU / 4 GiB). The
        # 'small' / 'large' classes will land as separate Job templates
        # (or as Cloud Workflows resource overrides) when sprint-08+
        # tightens the compute-class selection contract.
        resources {
          limits = {
            # job-0260 (demo finding): solves took 10-21 min at 4 CPU/4Gi.
            # SFINCS is OpenMP-parallel; 8 CPU + 16Gi roughly halves the
            # demo-grid wall clock. User-authorized live bump 2026-06-10.
            cpu    = "8"
            memory = "16Gi"
          }
        }

        # --- solver env ----------------------------------------------
        # GRACE2_RUN_ID + GRACE2_MANIFEST_URI are read by the entrypoint
        # as env-var fallbacks when CLI args aren't supplied. Empty
        # defaults keep the IaC declarative; actual values come from
        # `gcloud run jobs execute --args` (or the Cloud Workflow's
        # task-args override) at invocation time.
        env {
          name  = "GRACE2_RUN_ID"
          value = ""
        }
        env {
          name  = "GRACE2_MANIFEST_URI"
          value = ""
        }

        env {
          name  = "GCP_PROJECT"
          value = google_project.grace2.project_id
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = google_project.grace2.project_id
        }
        env {
          name  = "GRACE2_CACHE_BUCKET"
          value = google_storage_bucket.cache.name
        }
        env {
          name  = "GRACE2_RUNS_BUCKET"
          value = google_storage_bucket.runs.name
        }
      }
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_artifact_registry_repository.containers,
    google_storage_bucket.runs,
    google_storage_bucket.cache,
    google_storage_bucket_iam_member.sfincs_runtime_cache_viewer,
    google_storage_bucket_iam_member.sfincs_runtime_runs_admin,
    google_storage_bucket_iam_member.sfincs_runtime_qgs_viewer,
  ]
}

# --- Service account: workflow-invoker-sfincs -----------------------------
#
# Identity the Cloud Workflows execution runs under. The minimum-permission
# posture: it can READ from the runs bucket (poll completion.json), and it
# can RUN the sfincs-solver Job. Nothing else. The agent service does NOT
# use this identity; it submits `executions.create` against the workflow
# via its own agent-runtime SA (a binding that lands when the agent's
# run_solver tool wires up in job-0041 — until then this SA is dormant).

resource "google_service_account" "workflow_invoker_sfincs" {
  project      = google_project.grace2.project_id
  account_id   = "workflow-invoker-sfincs"
  display_name = "GRACE-2 SFINCS Cloud Workflows execution identity"
  description  = "Identity the grace-2-sfincs-orchestrator workflow runs under. Can invoke the sfincs-solver Job + read runs bucket. No other roles."

  depends_on = [google_project_service.enabled]
}

# --- IAM: workflow invoker can run the Cloud Run Job ---------------------
#
# Bound at the Job RESOURCE scope (mirror of job-0021 / job-0031 zero-project-
# grants discipline) — the SA cannot invoke any other Cloud Run service or
# Job in the project, only `grace-2-sfincs-solver`.
#
# Two bindings are required because Cloud Workflows calls the Job with env
# overrides (the workflow sets GRACE2_RUN_ID + GRACE2_MANIFEST_URI per
# execution), and the override path needs a stronger permission than vanilla
# invoke:
#   - `roles/run.invoker` grants `run.jobs.run` — the plain "kick the Job"
#     permission.
#   - `roles/run.developer` grants `run.jobs.runWithOverrides` — the
#     "kick the Job with per-execution env/arg overrides" permission, which
#     is what googleapis.run.v2.projects.locations.jobs.run requires when
#     the `body.overrides` field is set.
# Both are bound resource-scoped so the SA still can't touch any other Job
# or Service. This was diagnosed live from the first smoke-run execution
# (workflow PERMISSION_DENIED on run.jobs.runWithOverrides; workflow itself
# ran fine — Invariant 8 + FR-CE-2 cancel path will work the same way).

resource "google_cloud_run_v2_job_iam_member" "workflow_invoker_runs_job" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_job.sfincs_solver.location
  name     = google_cloud_run_v2_job.sfincs_solver.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow_invoker_sfincs.email}"
}

resource "google_cloud_run_v2_job_iam_member" "workflow_invoker_runs_job_developer" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_job.sfincs_solver.location
  name     = google_cloud_run_v2_job.sfincs_solver.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.workflow_invoker_sfincs.email}"
}

# --- IAM: workflow invoker can actAs the sfincs-runtime SA ----------------
#
# Calling `run.jobs.run` with overrides requires the caller to actAs the
# Job's runtime SA (Cloud Run security model: only callers explicitly
# authorized to "impersonate" the runtime SA may launch executions). Bound
# at the runtime SA resource scope — the workflow can only actAs THIS SA,
# not any other in the project.

resource "google_service_account_iam_member" "workflow_invoker_actas_sfincs_runtime" {
  service_account_id = google_service_account.sfincs_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.workflow_invoker_sfincs.email}"
}

# --- IAM: workflow invoker can read runs bucket (poll completion.json) ----
#
# Bucket-scoped objectViewer so the workflow's wait-and-collect step can
# poll `gs://<runs>/<run_id>/completion.json`. The workflow does NOT need
# write access — only the sfincs-runtime SA writes to the runs bucket.

resource "google_storage_bucket_iam_member" "workflow_invoker_runs_viewer" {
  bucket = google_storage_bucket.runs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.workflow_invoker_sfincs.email}"
}

# --- IAM: workflow invoker needs logging (Cloud Workflows runtime requirement) ---
#
# Workflows runtime writes execution-step logs under the calling SA. Without
# logWriter the execution silently truncates step logs (still functions, but
# the smoke-run evidence in job-0040 + job-0043 needs the step trail). This
# is project-scoped because Cloud Logging's log-bucket model has no
# resource-level binding for write-only on a single workflow's logs; the
# blast radius is bounded (write-only on the Cloud Logging project bucket).

resource "google_project_iam_member" "workflow_invoker_log_writer" {
  project = google_project.grace2.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.workflow_invoker_sfincs.email}"
}

# --- IAM: workflow invoker needs run.operations.get on Cloud Run LROs ----
#
# The googleapis.run.v2 connector's long-running-call wait semantics poll
# `projects/<proj>/locations/<region>/operations/<op-id>` — operations are
# created under the LOCATION resource, not the Job resource. There is no
# resource-scoped binding that exposes operations.get on operations created
# from a specific Job; the operation's parent is the location. `roles/run.viewer`
# at PROJECT scope is the smallest binding that closes the wiring loop —
# it's read-only on Cloud Run (no write/delete/invoke), and the alternative
# (a custom role with run.operations.get + run.jobs.executions.get only) is
# bookkeeping over a binding that's already read-only.
#
# Diagnosed live from the second smoke-run execution (workflow ran the Job
# successfully — Job execution completed, completion.json appeared in the
# runs bucket — but the workflow's long-running-call poll returned
# PERMISSION_DENIED on `run.operations.get`, so the workflow's return value
# reflects the error even though the Job itself succeeded end-to-end).

resource "google_project_iam_member" "workflow_invoker_run_viewer" {
  project = google_project.grace2.project_id
  role    = "roles/run.viewer"
  member  = "serviceAccount:${google_service_account.workflow_invoker_sfincs.email}"
}

# --- Cloud Workflow: grace-2-sfincs-orchestrator -------------------------
#
# Three-step workflow per FR-CE-2:
#   1. validate input — assert run_id + manifest_uri are present.
#   2. invoke-cloud-run-job — call googleapis.run.v2 Jobs.run with task
#      overrides setting GRACE2_RUN_ID + GRACE2_MANIFEST_URI env vars.
#      Waits (long-running call) for the Job execution to reach terminal
#      state. Cloud Workflows native long-running-call semantics handle
#      polling; the workflow execution completes when the Job execution
#      does.
#   3. wait-and-collect — read the completion.json the entrypoint wrote
#      to gs://<runs>/<run_id>/completion.json and return its parsed
#      contents as the workflow result. If the JSON read fails (the
#      entrypoint crashed before writing), return a synthetic error
#      envelope so wait_for_completion (job-0041) doesn't poll forever.
#
# Cancellation: a `googleapis.workflows.executions.cancel` against this
# workflow's running execution propagates to the Cloud Run Job invocation
# in step 2 (Workflows native long-running-call cancellation). This is the
# Invariant 8 cancel path the agent.run_solver tool will plug into via
# the schema-owned ExecutionHandle.workflows_execution_id field.

resource "google_workflows_workflow" "sfincs_orchestrator" {
  project         = google_project.grace2.project_id
  region          = var.gcp_region
  name            = "grace-2-sfincs-orchestrator"
  description     = "FR-CE-2 orchestration for the SFINCS solver Cloud Run Job. Invoked by agent.run_solver (job-0041); cancellation propagates to the running Job execution (Invariant 8)."
  service_account = google_service_account.workflow_invoker_sfincs.id

  labels = local.sfincs_labels

  # Workflows YAML expression-syntax notes:
  #   - Cloud Workflows reads `${expr}` as a runtime expression. Terraform
  #     heredocs interpolate `${...}` too, so we double-dollar (`$${...}`)
  #     EVERY workflow-runtime expression to escape it past Terraform. Only
  #     the two Terraform-substituted strings — the project/region/job-name
  #     URL and the runs-bucket name — use single-dollar `${...}`.
  #   - Expressions that contain string literals with operators (the kind
  #     Workflows' YAML parser would otherwise see as YAML structure) MUST
  #     be wrapped in single quotes. The Cloud Workflows error message on
  #     bad parse names the exact remediation ("wrap with single quotes").
  source_contents = <<-EOT
    # GRACE-2 SFINCS orchestrator (sprint-07 / M5 / FR-CE-2 / job-0040).
    #
    # Inputs (passed as the executions.create `argument` JSON):
    #   {
    #     "run_id":       "<run identifier>",
    #     "manifest_uri": "gs://<bucket>/<path>/setup.json"
    #   }
    #
    # Output: parsed completion.json the entrypoint wrote to the runs bucket.

    main:
      params: [args]
      steps:
        - validate:
            switch:
              - condition: '$${not("run_id" in args)}'
                raise: "INVALID_INPUT: run_id is required"
              - condition: '$${not("manifest_uri" in args)}'
                raise: "INVALID_INPUT: manifest_uri is required"
        - invoke_sfincs_job:
            try:
              call: googleapis.run.v2.projects.locations.jobs.run
              args:
                name: "projects/${google_project.grace2.project_id}/locations/${var.gcp_region}/jobs/${google_cloud_run_v2_job.sfincs_solver.name}"
                connector_params:
                  timeout: 2400
                body:
                  overrides:
                    containerOverrides:
                      - env:
                          - name: GRACE2_RUN_ID
                            value: '$${args.run_id}'
                          - name: GRACE2_MANIFEST_URI
                            value: '$${args.manifest_uri}'
              result: job_execution
            except:
              as: e
              steps:
                - log_job_error:
                    call: sys.log
                    args:
                      data: '$${e}'
                      severity: "ERROR"
                - return_job_error:
                    return:
                      run_id: '$${args.run_id}'
                      status: "error"
                      error: '$${"sfincs job execution failed: " + json.encode_to_string(e)}'
        - read_completion:
            try:
              call: googleapis.storage.v1.objects.get
              args:
                bucket: "${google_storage_bucket.runs.name}"
                object: '$${args.run_id + "/completion.json"}'
                alt: "media"
              result: completion
            except:
              as: e
              steps:
                - log_completion_error:
                    call: sys.log
                    args:
                      data: '$${e}'
                      severity: "ERROR"
                - return_missing_completion:
                    return:
                      run_id: '$${args.run_id}'
                      status: "error"
                      error: "completion.json missing; entrypoint did not finalize"
        - return_completion:
            return: '$${completion}'
  EOT

  depends_on = [
    google_project_service.enabled,
    google_cloud_run_v2_job.sfincs_solver,
    google_storage_bucket.runs,
    google_cloud_run_v2_job_iam_member.workflow_invoker_runs_job,
    google_cloud_run_v2_job_iam_member.workflow_invoker_runs_job_developer,
    google_service_account_iam_member.workflow_invoker_actas_sfincs_runtime,
    google_storage_bucket_iam_member.workflow_invoker_runs_viewer,
    google_project_iam_member.workflow_invoker_log_writer,
  ]
}
