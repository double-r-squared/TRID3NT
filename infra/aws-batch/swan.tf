# swan.tf -- SWAN (Simulating WAves Nearshore) AWS Batch job definition + ECR repo
# (SWAN Phase 1).
#
# FILE-ONLY SCAFFOLD (NOT applied in Phase 1). SWAN (the TU Delft third-generation
# spectral nearshore wave solver) is a NEW Batch user (after SFINCS, SWMM, MODFLOW,
# GeoClaw). Per DESIGN INVARIANT 2 in main.tf ("the compute environment and queue
# are ENGINE-AGNOSTIC ... each gets its own job definition on the SAME compute
# environment and queue"), this file adds ONLY the SWAN-scoped resources,
# mirroring geoclaw.tf / swmm.tf / modflow.tf exactly:
#
#   - a grace2-swan ECR repository (the GPL SWAN worker image --
#     services/workers/swan/Dockerfile -- is pushed here)
#   - an aws_batch_job_definition.swan pointing at that image, on the SAME
#     grace2-solvers queue + grace2-solvers-spot compute environment + the SAME
#     grace2-batch-job-task-role IAM (all defined in main.tf)
#
# Additive only (ECR repo + lifecycle + job def). The image is built+pushed
# separately via the SHARED grace2-worker-builder CodeBuild project
# (WORKER_DIR=swan, ECR_REPO=grace2-swan; the SWAN Fortran compiles for x86_64),
# and the agent stays inert until GRACE2_AWS_BATCH_JOB_DEF_SWAN is set on the box
# (registering a job def that references an unpushed tag is harmless -- Batch
# resolves the image at job start, not at registration). SWAN's
# run_swan.register_swan_solver requires a SWAN-OWN job-def (it routes via
# GRACE2_AWS_BATCH_JOB_DEF_SWAN), so SWAN never cross-routes into the SFINCS image
# off the generic GRACE2_AWS_BATCH_JOB_DEF.
#
# Per-solver routing seam: after apply + push, set
# GRACE2_AWS_BATCH_JOB_DEF_SWAN = job_definition_name_swan (output below) on the
# agent EC2 box; SFINCS/SWMM/MODFLOW/GeoClaw routing is unchanged.
#
# GATED STEP (orchestrator + NATE): this .tf is authored but NOT applied; the
# tofu apply + the off-box image build/push + the env flip are the SEPARATE gated
# live-AWS step. Nothing here runs in Phase 1.

variable "swan_ecr_repo_name" {
  type        = string
  description = "Name for the ECR repository that will hold the SWAN (TU Delft spectral wave) worker image. SWAN is a new Batch user; its own image lives in its own repo."
  default     = "grace2-swan"
}

# -----------------------------------------------------------------------------
# ECR REPOSITORY -- grace2-swan
# Mirror of aws_ecr_repository.geoclaw/swmm/modflow (scan-on-push + a 10-image cap).
# -----------------------------------------------------------------------------

resource "aws_ecr_repository" "swan" {
  name                 = var.swan_ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # NOTE: AWS ECR tag VALUES reject parentheses (allowed: letters, digits,
  # spaces, and + - = . _ : / @). Keep the description unparenthesized or
  # CreateRepository fails with InvalidTagParameterException.
  tags = {
    description = "SWAN TU Delft spectral nearshore wave solver worker image - services/workers/swan/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "swan" {
  repository = aws_ecr_repository.swan.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the last 10 pushed images; expire older ones to cap storage cost."
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# BATCH JOB DEFINITION -- grace2-swan
#
# SWAN-SPECIFIC. References the SAME queue + compute environment + job-task role
# from main.tf -- only the image, env, and command differ from grace2-sfincs.
#
# BASELINE sizing 8 vCPU / 16384 MiB (the "standard" compute class). SWAN's
# OpenMP spectral solve parallelizes over the grid; the agent's
# _run_solver_aws_batch overrides vcpu/memory PER JOB via containerOverrides, so
# the baseline here is just a fallback / documentation. OMP_NUM_THREADS is set to
# the vCPU count by the entrypoint launch (swanrun -omp $OMP_NUM_THREADS).
#
# Environment:
#   GRACE2_OBJECT_STORE=s3   -- the scheme-aware entrypoint routes object I/O
#                               through boto3 (s3://).
#   GRACE2_RUNS_BUCKET       -- the runs bucket; also overridden per-job by the
#                               agent (belt-and-suspenders, entrypoint parity).
#   OMP_NUM_THREADS          -- the OpenMP thread count; the agent overrides it
#                               per-job to match the chosen compute class vCPUs.
# -----------------------------------------------------------------------------

resource "aws_batch_job_definition" "swan" {
  name = "grace2-swan"
  type = "container"

  container_properties = jsonencode({
    # The SWAN worker image (grace2-swan ECR repo, :latest tag). Batch pulls the
    # tag on each job start; use digest pinning for production repro.
    image = "${aws_ecr_repository.swan.repository_url}:latest"

    # Baseline resource allocation -- the agent overrides these per-job.
    #   small:     4 vCPU /  8192 MiB
    #   standard:  8 vCPU / 16384 MiB  (default)
    #   large:    16 vCPU / 32768 MiB
    #   xlarge:   48 vCPU / 98304 MiB
    resourceRequirements = [
      { type = "VCPU", value = "8" },
      { type = "MEMORY", value = "16384" },
    ]

    # Reuse the SAME job-task role main.tf created (S3 runs+cache access + ECS
    # execution). Engine-agnostic -- no SWAN-specific IAM is required.
    jobRoleArn       = aws_iam_role.job_task.arn
    executionRoleArn = aws_iam_role.job_task.arn

    environment = [
      { name = "GRACE2_OBJECT_STORE", value = "s3" },
      { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
      { name = "OMP_NUM_THREADS", value = "8" },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "AWS_REGION", value = var.region },
    ]

    # Placeholder command -- overridden per-job by the agent's containerOverrides
    # with ["--run-id", "<run_id>", "--manifest-uri", "<s3_uri>"]. The entrypoint
    # requires --run-id, so the placeholder returns a clean non-zero at
    # registration (Batch only needs a non-empty command array).
    command = ["--run-id", "placeholder", "--manifest-uri", "placeholder"]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "swan"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  # Timeout: 7200 s (2 hours) -- a nonstationary SWAN hurricane wave hindcast over
  # a large domain can run longer than the 1h SFINCS/SWMM/MODFLOW budget; the
  # agent's per-job timeout still bounds it tighter when needed. Stationary runs
  # are seconds-to-minutes.
  timeout {
    attempt_duration_seconds = 7200
  }
}

# -----------------------------------------------------------------------------
# OUTPUTS
# -----------------------------------------------------------------------------

output "job_definition_name_swan" {
  description = "Name of the SWAN Batch job definition. Set GRACE2_AWS_BATCH_JOB_DEF_SWAN to this on the agent box."
  value       = aws_batch_job_definition.swan.name
}

output "ecr_repository_url_swan" {
  description = "Full ECR repository URL for the SWAN worker image. Use this as the image tag base when building/pushing services/workers/swan/Dockerfile."
  value       = aws_ecr_repository.swan.repository_url
}
