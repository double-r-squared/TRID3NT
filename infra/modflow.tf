# modflow.tf — MODFLOW 6 solver Cloud Run Job + Cloud Workflows.
#
# Sprint-13 / MOD-1 / job-0220. The Case 2 groundwater substrate: a
# containerized MODFLOW 6 (mf6 6.5.0) solver (services/workers/modflow/)
# running as a Cloud Run Job named `grace-2-modflow-solver`, orchestrated by
# Cloud Workflows `grace-2-modflow-orchestrator` which the agent service
# submits `executions.create` against. Outputs land in the SAME
# `gs://grace-2-hazard-prod-runs/` bucket the SFINCS solver writes to (the
# `google_storage_bucket.runs` resource provisioned in infra/sfincs.tf) — runs
# hold persisted solver outputs per FR-CE-3 regardless of engine.
#
# This file is the direct MODFLOW analogue of infra/sfincs.tf. It does NOT
# re-declare the runs bucket (one runs bucket serves all solvers); it
# provisions a dedicated runtime SA, a dedicated workflow-invoker SA, the
# Cloud Run Job, and the orchestrator workflow, all with MODFLOW-specific
# sizing (design doc § 4).
#
# Sizing differences from SFINCS (design doc § 4):
#   - memory 8Gi (vs 4Gi): MODFLOW 6 with GWT loads full head + concentration
#     arrays across all time steps in working memory; 4 GiB is too tight once
#     the agent-side rasterio postprocess runs. 8 GiB is the demo-scale ceiling
#     (OQ-MOD-4 — revisit for finer grids).
#   - task_timeout 7200s (vs 1800s): transport runs take 10-60 min; 2 h is the
#     demo budget ceiling.
#   - workflow connector timeout 8400s (vs 2400s): 2 h task budget + 10 min
#     buffer for cold-start + LRO poll.
#
# Image source-of-truth (mirror of infra/sfincs.tf digest discipline):
#   the Cloud Run Job pins the container image BY DIGEST below, not by the
#   `:latest` tag, so `tofu plan` detects when a newer image has been pushed
#   without an IaC change.
#
#   Bump-on-build workflow:
#     1. `make modflow-build` (Cloud Build push) emits the new digest at AR.
#        Read it via:
#          gcloud artifacts docker images list \
#            us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-containers \
#            --include-tags | grep modflow-solver
#     2. Update the digest on the `modflow_image_digest` local below.
#     3. `tofu apply` rolls the Job to the new image.
#     4. `tofu plan` after must return "No changes".
#
# Invariant compliance (mirror of infra/sfincs.tf):
#   - Invariant 5 (Tier separation): runs bucket is UBA + PAP enforced + no
#     public IAM (declared in infra/sfincs.tf). Outputs reach the client via
#     QGIS Server (COG rendering) or agent envelope (JSON metadata).
#   - Invariant 6 (Metadata-payload pattern): the workflow does not enumerate
#     the runs bucket — it writes by known run_id and reads completion.json by
#     known path.
#   - Invariant 8 (Cancellation is first-class): Cloud Workflows
#     `executions.cancel` propagates to the running Cloud Run Job execution;
#     the agent's run_modflow_job (job-0227) carries the workflow execution id
#     in ExecutionHandle.workflows_execution_id (Appendix A; schema-owned).
#   - NFR-S-2 / NFR-S-3 (credentials posture): SA grants are BUCKET-SCOPED.
#   - NFR-C-2 (scale-to-zero): Cloud Run Jobs are inherently scale-to-zero;
#     no min instances configured.
#
# Labels (NFR-C-1 idle-cost breakdown): sprint=13 + component=modflow-solver.

locals {
  modflow_labels = merge(local.common_labels, {
    component = "modflow-solver"
    sprint    = "13"
  })

  # Pinned image digest (bump-on-build per the workflow above).
  #
  # PLACEHOLDER until the first `make modflow-build` resolves a real digest.
  # This box has no reachable docker daemon + no gcloud, so the image has NOT
  # been built/pushed in this job (BLOCKED-ENV — see
  # reports/inflight/job-0220-infra-20260609/report.md § "User unblock steps").
  # `tofu validate` passes with a placeholder digest (validate does not touch
  # the registry); `tofu apply` will fail to pull until the real digest is
  # recorded here after the Cloud Build. The Dockerfile + host mf6 6.5.0 smoke
  # run (evidence/mf6_smoke.log) prove the image contents are sound; only the
  # AR push + digest pin remain, gated on the user's gcloud/docker unblock.
  modflow_image_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}

# --- Runs bucket ----------------------------------------------------------
#
# NOT re-declared here. The MODFLOW solver writes to the SAME runs bucket the
# SFINCS solver uses — `google_storage_bucket.runs` in infra/sfincs.tf. One
# runs bucket serves all solver engines (FR-CE-3 / FR-MP-3); each Cloud Run
# Job execution writes under its own `<run_id>/` prefix, so there is no
# collision and no need for an engine-specific bucket. The IAM grants below
# reference `google_storage_bucket.runs` directly.

# --- Service account: modflow-runtime -------------------------------------
#
# Dedicated runtime SA for the MODFLOW Cloud Run Job (design doc § 4 — no
# reuse of sfincs-runtime). The SA exists ONLY for this Job; no keys are
# minted (Cloud Run attaches the runtime identity via the metadata server).

resource "google_service_account" "modflow_runtime" {
  project      = google_project.grace2.project_id
  account_id   = "modflow-runtime"
  display_name = "GRACE-2 MODFLOW 6 solver Cloud Run Job runtime"
  description  = "Cloud Run Job identity for the MODFLOW 6 solver. objectViewer on -cache; objectAdmin on -runs. No -qgs grant (MODFLOW does not touch the QGIS project store). No project-wide roles."

  depends_on = [google_project_service.enabled]
}

# --- IAM: bucket-scoped objectViewer on -cache (read FloPy deck inputs) ----
#
# The agent's deck-uploader stages the FloPy-generated GWF+GWT deck +
# manifest under gs://<...>-cache/modflow/<run_id>/. objectViewer grants
# storage.objects.get + .list on THIS bucket only.

resource "google_storage_bucket_iam_member" "modflow_runtime_cache_viewer" {
  bucket = google_storage_bucket.cache.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.modflow_runtime.email}"
}

# --- IAM: bucket-scoped objectAdmin on -runs (write outputs) --------------
#
# The MODFLOW solver writes heads/concentration outputs + completion.json under
# gs://<...>-runs/<run_id>/. objectAdmin at bucket scope authorizes the
# per-execution writes. Shared with the SFINCS runtime SA, but each writes only
# its own run_id prefix.

resource "google_storage_bucket_iam_member" "modflow_runtime_runs_admin" {
  bucket = google_storage_bucket.runs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.modflow_runtime.email}"
}

# NOTE: no -qgs viewer grant (unlike sfincs-runtime). MODFLOW decks are
# FloPy-generated text files staged through the cache bucket; the solver does
# not read the canonical .qgs project store (design doc § 4).

# --- Cloud Run v2 Job: grace-2-modflow-solver -----------------------------

resource "google_cloud_run_v2_job" "modflow_solver" {
  project  = google_project.grace2.project_id
  name     = "grace-2-modflow-solver"
  location = var.gcp_region

  labels = local.modflow_labels

  template {
    labels = local.modflow_labels

    # Mirror of the SFINCS smoke-pattern sizing. max_retries=1 — MODFLOW is
    # idempotent on the runs bucket (entrypoint clears scratch on start), so a
    # retry on a transient I/O error overwrites partial outputs cleanly.
    # NFR-C-2 (scale-to-zero) is automatic for Cloud Run Jobs.
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.modflow_runtime.email

      # 2-hour task timeout (design doc § 4). MODFLOW transport runs take
      # 10-60 min depending on grid size + time-step count; 2 h is the demo
      # budget ceiling with headroom for cold container pull + mf6 startup.
      timeout     = "7200s"
      max_retries = 1

      containers {
        # Digest-pinned (mirror of infra/sfincs.tf discipline). Bump per the
        # workflow in the file header when `make modflow-build` produces a new
        # digest. PLACEHOLDER digest until the first Cloud Build (BLOCKED-ENV
        # on this box — no docker daemon + no gcloud).
        image = "${var.gcp_region}-docker.pkg.dev/${google_project.grace2.project_id}/${google_artifact_registry_repository.containers.repository_id}/grace-2-modflow-solver@${local.modflow_image_digest}"

        # 8 GiB / 4 vCPU (design doc § 4). 8 GiB because MODFLOW 6 with GWT
        # loads full head + concentration arrays in working memory; 4 GiB
        # (the SFINCS baseline) is too tight once rasterio postprocess runs.
        # OQ-MOD-4: revisit for finer demo grids.
        resources {
          limits = {
            cpu    = "4"
            memory = "8Gi"
          }
        }

        # --- solver env ----------------------------------------------
        # GRACE2_RUN_ID + GRACE2_MANIFEST_URI are read by the entrypoint as
        # env-var fallbacks; actual values come from the Cloud Workflow's
        # task-args override at invocation time. Empty defaults keep the IaC
        # declarative.
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
    google_storage_bucket_iam_member.modflow_runtime_cache_viewer,
    google_storage_bucket_iam_member.modflow_runtime_runs_admin,
  ]
}

# --- Service account: workflow-invoker-modflow ----------------------------
#
# Identity the Cloud Workflows execution runs under (design doc § 4 — dedicated,
# no reuse of workflow-invoker-sfincs). Minimum-permission posture: it can READ
# from the runs bucket (poll completion.json) and RUN the modflow-solver Job.
# Nothing else.

resource "google_service_account" "workflow_invoker_modflow" {
  project      = google_project.grace2.project_id
  account_id   = "workflow-invoker-modflow"
  display_name = "GRACE-2 MODFLOW Cloud Workflows execution identity"
  description  = "Identity the grace-2-modflow-orchestrator workflow runs under. Can invoke the modflow-solver Job + read runs bucket. No other roles."

  depends_on = [google_project_service.enabled]
}

# --- IAM: workflow invoker can run the Cloud Run Job ----------------------
#
# Bound at the Job RESOURCE scope. Two bindings (mirror of infra/sfincs.tf —
# this was diagnosed live on the SFINCS smoke run): run.invoker grants
# run.jobs.run; run.developer grants run.jobs.runWithOverrides, required when
# the workflow sets body.overrides (GRACE2_RUN_ID + GRACE2_MANIFEST_URI env).

resource "google_cloud_run_v2_job_iam_member" "workflow_invoker_modflow_runs_job" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_job.modflow_solver.location
  name     = google_cloud_run_v2_job.modflow_solver.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow_invoker_modflow.email}"
}

resource "google_cloud_run_v2_job_iam_member" "workflow_invoker_modflow_runs_job_developer" {
  project  = google_project.grace2.project_id
  location = google_cloud_run_v2_job.modflow_solver.location
  name     = google_cloud_run_v2_job.modflow_solver.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.workflow_invoker_modflow.email}"
}

# --- IAM: workflow invoker can actAs the modflow-runtime SA ----------------
#
# Calling run.jobs.run with overrides requires the caller to actAs the Job's
# runtime SA. Bound at the runtime SA resource scope (mirror of
# infra/sfincs.tf).

resource "google_service_account_iam_member" "workflow_invoker_actas_modflow_runtime" {
  service_account_id = google_service_account.modflow_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.workflow_invoker_modflow.email}"
}

# --- IAM: workflow invoker can read runs bucket (poll completion.json) -----
#
# Bucket-scoped objectViewer so the workflow's read-completion step can poll
# gs://<runs>/<run_id>/completion.json. No write access — only modflow-runtime
# writes to the runs bucket.

resource "google_storage_bucket_iam_member" "workflow_invoker_modflow_runs_viewer" {
  bucket = google_storage_bucket.runs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.workflow_invoker_modflow.email}"
}

# --- IAM: workflow invoker needs logging (Cloud Workflows runtime req) -----
#
# Workflows runtime writes execution-step logs under the calling SA. Project-
# scoped because Cloud Logging's log-bucket model has no resource-level
# write-only binding for a single workflow's logs (mirror of infra/sfincs.tf;
# the blast radius is bounded — write-only on the Cloud Logging project bucket).

resource "google_project_iam_member" "workflow_invoker_modflow_log_writer" {
  project = google_project.grace2.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.workflow_invoker_modflow.email}"
}

# --- IAM: workflow invoker needs run.operations.get on Cloud Run LROs ------
#
# The googleapis.run.v2 connector's long-running-call wait semantics poll
# operations created under the LOCATION resource. roles/run.viewer at PROJECT
# scope is the smallest binding that closes the wiring loop (read-only on Cloud
# Run). Diagnosed live on the SFINCS smoke run (PERMISSION_DENIED on
# run.operations.get without it); the MODFLOW workflow uses the identical
# connector path so the same binding is required.

resource "google_project_iam_member" "workflow_invoker_modflow_run_viewer" {
  project = google_project.grace2.project_id
  role    = "roles/run.viewer"
  member  = "serviceAccount:${google_service_account.workflow_invoker_modflow.email}"
}

# --- Cloud Workflow: grace-2-modflow-orchestrator -------------------------
#
# Three-step workflow per FR-CE-2, structurally identical to the SFINCS
# orchestrator (infra/sfincs.tf). Differs only in the Job name it targets and
# the connector_params.timeout (8400 = 2 h task budget + 10 min buffer, vs the
# SFINCS 2400). The completion.json schema the entrypoint writes carries the
# MODFLOW-specific `converged` + `model_crs` fields, but the workflow returns
# the parsed JSON verbatim, so no workflow-side change is needed.
#
# Cancellation: a googleapis.workflows.executions.cancel against this
# workflow's running execution propagates to the Cloud Run Job invocation in
# step 2 (Workflows native long-running-call cancellation) — the Invariant 8
# cancel path the agent's run_modflow_job tool plugs into via the schema-owned
# ExecutionHandle.workflows_execution_id field.

resource "google_workflows_workflow" "modflow_orchestrator" {
  project         = google_project.grace2.project_id
  region          = var.gcp_region
  name            = "grace-2-modflow-orchestrator"
  description     = "FR-CE-2 orchestration for the MODFLOW 6 solver Cloud Run Job. Invoked by agent.run_modflow_job (job-0227); cancellation propagates to the running Job execution (Invariant 8)."
  service_account = google_service_account.workflow_invoker_modflow.id

  labels = local.modflow_labels

  # Workflows YAML expression-syntax notes (mirror of infra/sfincs.tf):
  #   - Cloud Workflows reads `${expr}` as a runtime expression. Terraform
  #     heredocs interpolate `${...}` too, so we double-dollar (`$${...}`)
  #     EVERY workflow-runtime expression to escape it past Terraform. Only the
  #     Terraform-substituted strings — the project/region/job-name URL and the
  #     runs-bucket name — use single-dollar `${...}`.
  #   - Expressions containing string literals with operators MUST be wrapped
  #     in single quotes (Cloud Workflows YAML-parser requirement).
  source_contents = <<-EOT
    # GRACE-2 MODFLOW orchestrator (sprint-13 / MOD-1 / FR-CE-2 / job-0220).
    #
    # Inputs (passed as the executions.create `argument` JSON):
    #   {
    #     "run_id":       "<run identifier>",
    #     "manifest_uri": "gs://<bucket>/modflow/<run_id>/manifest.json"
    #   }
    #
    # Output: parsed completion.json the entrypoint wrote to the runs bucket
    # (carries the MODFLOW-specific `converged` + `model_crs` fields).

    main:
      params: [args]
      steps:
        - validate:
            switch:
              - condition: '$${not("run_id" in args)}'
                raise: "INVALID_INPUT: run_id is required"
              - condition: '$${not("manifest_uri" in args)}'
                raise: "INVALID_INPUT: manifest_uri is required"
        - invoke_mf6_job:
            try:
              call: googleapis.run.v2.projects.locations.jobs.run
              args:
                name: "projects/${google_project.grace2.project_id}/locations/${var.gcp_region}/jobs/${google_cloud_run_v2_job.modflow_solver.name}"
                connector_params:
                  timeout: 8400
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
                      error: '$${"modflow job execution failed: " + json.encode_to_string(e)}'
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
    google_cloud_run_v2_job.modflow_solver,
    google_storage_bucket.runs,
    google_cloud_run_v2_job_iam_member.workflow_invoker_modflow_runs_job,
    google_cloud_run_v2_job_iam_member.workflow_invoker_modflow_runs_job_developer,
    google_service_account_iam_member.workflow_invoker_actas_modflow_runtime,
    google_storage_bucket_iam_member.workflow_invoker_modflow_runs_viewer,
    google_project_iam_member.workflow_invoker_modflow_log_writer,
  ]
}
