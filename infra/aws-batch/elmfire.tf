# elmfire.tf -- ELMFIRE (Eulerian Level set Model of FIRE spread) AWS Batch job
# definition + ECR repo (FIRE-4).
#
# ELMFIRE is the wildfire-spread engine (design:
# reports/design/elmfire-engine-2026-07-07.md; container proof:
# reports/inflight/fire-1-container-proof.md). Per DESIGN INVARIANT 2 in
# main.tf ("the compute environment and queue are ENGINE-AGNOSTIC ... each
# gets its own job definition on the SAME compute environment and queue"),
# this file adds ONLY the ELMFIRE-scoped resources, mirroring
# swan.tf / geoclaw.tf / swmm.tf exactly:
#
#   - a grace2-elmfire ECR repository (services/workers/elmfire/Dockerfile)
#   - an aws_batch_job_definition.elmfire pointing at that image, on the SAME
#     grace2-solvers queue + Spot compute environment + the SAME
#     grace2-batch-job-task-role IAM (all defined in main.tf)
#
# Additive only -- NO new compute environment, NO always-on compute: a Batch
# job definition and an ECR image are both zero-idle-cost, and jobs place on
# the existing scale-to-zero Spot CE.
#
# IAM: the shared job-task role already grants exactly what this worker needs
# (cache-bucket read for the staged deck, runs-bucket write for outputs +
# completion.json, CloudWatch logs via the CE instance role). NOTHING
# ELMFIRE-specific is added.
#
# Per-solver routing seam: the job-definition NAME below MUST equal
# run_elmfire.ELMFIRE_BATCH_JOB_DEF_NAME ("grace2-elmfire"). Activation switch
# = GRACE2_AWS_BATCH_JOB_DEF_ELMFIRE on the agent (set in
# infra/aws-agent-isolation/ecs.tf); until it is set the agent's Batch lane
# stays inert (solver.py::_resolve_batch_job_def). ELMFIRE never cross-routes
# into the SFINCS image off the generic GRACE2_AWS_BATCH_JOB_DEF because
# run_elmfire requires its OWN job def.

variable "elmfire_ecr_repo_name" {
  type        = string
  description = "Name for the ECR repository that will hold the ELMFIRE wildfire-spread worker image."
  default     = "grace2-elmfire"
}

# -----------------------------------------------------------------------------
# ECR REPOSITORY -- grace2-elmfire
# Mirror of aws_ecr_repository.swan/geoclaw/swmm (scan-on-push + a 10-image cap).
# -----------------------------------------------------------------------------

resource "aws_ecr_repository" "elmfire" {
  name                 = var.elmfire_ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # NOTE: AWS ECR tag VALUES reject parentheses (allowed: letters, digits,
  # spaces, and + - = . _ : / @). Keep the description unparenthesized.
  tags = {
    description = "ELMFIRE Eulerian level set wildfire-spread solver worker image - services/workers/elmfire/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "elmfire" {
  repository = aws_ecr_repository.elmfire.name

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
# BATCH JOB DEFINITION -- grace2-elmfire
#
# ELMFIRE-SPECIFIC. References the SAME queue + compute environment + job-task
# role from main.tf -- only the image, env, and log prefix differ.
#
# BASELINE sizing 4 vCPU / 8192 MiB (the "small" compute class): a
# deterministic county-scale 30 m run is seconds-to-minutes (FIRE-1 evidence:
# 400x400 x 6 h in 4.3 s; 2400x2400 x 7 h in 67 s on 4 cpus) and the
# 2025.0526 gnu_mpi build runs single-rank (no OpenMP flag in
# Makefile_elmfire), so extra vCPUs buy little today. The agent's
# _run_solver_aws_batch overrides vcpu/memory PER JOB via containerOverrides;
# the baseline here is just a fallback. Monte Carlo ensembles (FIRE-5+) map
# onto Batch array jobs, one member per task -- same job def.
# -----------------------------------------------------------------------------

resource "aws_batch_job_definition" "elmfire" {
  name = "grace2-elmfire"
  type = "container"

  container_properties = jsonencode({
    # The ELMFIRE worker image (grace2-elmfire ECR repo, :latest tag). Batch
    # pulls the tag at job start, not at registration.
    image = "${aws_ecr_repository.elmfire.repository_url}:latest"

    resourceRequirements = [
      { type = "VCPU", value = "4" },
      { type = "MEMORY", value = "8192" },
    ]

    # Reuse the SAME job-task role main.tf created (S3 runs+cache access + ECS
    # execution). Engine-agnostic -- no ELMFIRE-specific IAM is required.
    jobRoleArn       = aws_iam_role.job_task.arn
    executionRoleArn = aws_iam_role.job_task.arn

    environment = [
      { name = "GRACE2_OBJECT_STORE", value = "s3" },
      { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "AWS_REGION", value = var.region },
    ]

    # Placeholder command -- overridden per-job by the agent's
    # containerOverrides with ["--run-id", "<run_id>", "--manifest-uri",
    # "<s3_uri>"]. The entrypoint requires --run-id, so the placeholder
    # returns a clean non-zero at registration (Batch only needs a non-empty
    # command array).
    command = ["--run-id", "placeholder", "--manifest-uri", "placeholder"]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "elmfire"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  # Timeout: 3600 s -- generous for a solver whose FIRE-1-calibrated runtime is
  # seconds-to-minutes at county scale; the agent's per-job timeout bounds it
  # tighter when needed.
  timeout {
    attempt_duration_seconds = 3600
  }
}

# -----------------------------------------------------------------------------
# OUTPUTS
# -----------------------------------------------------------------------------

output "job_definition_name_elmfire" {
  description = "Name of the ELMFIRE Batch job definition. Set GRACE2_AWS_BATCH_JOB_DEF_ELMFIRE to this on the agent (infra/aws-agent-isolation/ecs.tf)."
  value       = aws_batch_job_definition.elmfire.name
}

output "ecr_repository_url_elmfire" {
  description = "Full ECR repository URL for the ELMFIRE worker image. Use this as the image tag base when building/pushing services/workers/elmfire/Dockerfile."
  value       = aws_ecr_repository.elmfire.repository_url
}
