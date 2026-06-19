# modflow.tf — MODFLOW 6 (FloPy + mf6) AWS Batch job definition + ECR repo.
#
# MODFLOW is the SECOND non-SFINCS Batch user (after SWMM). Per DESIGN INVARIANT
# 2 in main.tf ("the compute environment and queue are ENGINE-AGNOSTIC ... each
# gets its own job definition on the SAME compute environment and queue"), this
# file adds ONLY the MODFLOW-scoped resources, mirroring swmm.tf exactly:
#
#   - a grace2-modflow ECR repository (the mf6 worker image —
#     services/workers/modflow/Dockerfile, ported to scheme-aware S3 — is pushed
#     here)
#   - an aws_batch_job_definition.modflow pointing at that image, on the SAME
#     grace2-solvers queue + grace2-solvers-spot compute environment + the SAME
#     grace2-batch-job-task-role IAM (all defined in main.tf)
#
# Additive only (ECR repo + lifecycle + job def). The image is pushed separately
# and the agent stays inert until GRACE2_AWS_BATCH_JOB_DEF_MODFLOW is set on the
# box (registering a job def that references an unpushed tag is harmless — Batch
# resolves the image at job start). The agent's is_batch_mode() gate (run_modflow
# .py) requires a MODFLOW-OWN job-def, so MODFLOW never cross-routes into the
# SFINCS image off the generic GRACE2_AWS_BATCH_JOB_DEF.
#
# Per-solver routing seam: after apply + push, set GRACE2_AWS_BATCH_JOB_DEF_MODFLOW
# = job_definition_name_modflow (output below) on the agent EC2 box; SFINCS/SWMM
# routing is unchanged.

variable "modflow_ecr_repo_name" {
  type        = string
  description = "Name for the ECR repository that will hold the MODFLOW (mf6/FloPy) worker image. MODFLOW is the second non-SFINCS Batch user; its own image lives in its own repo."
  default     = "grace2-modflow"
}

# ─────────────────────────────────────────────────────────────────────────────
# ECR REPOSITORY — grace2-modflow
# Mirror of aws_ecr_repository.swmm (scan-on-push + a 10-image lifecycle cap).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "modflow" {
  name                 = var.modflow_ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # NOTE: AWS ECR tag VALUES reject parentheses (allowed: letters, digits,
  # spaces, and + - = . _ : / @). Keep the description unparenthesized or
  # CreateRepository fails with InvalidTagParameterException.
  tags = {
    description = "MODFLOW 6 mf6/FloPy solver worker image - services/workers/modflow/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "modflow" {
  repository = aws_ecr_repository.modflow.name

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

# ─────────────────────────────────────────────────────────────────────────────
# BATCH JOB DEFINITION — grace2-modflow
#
# MODFLOW-SPECIFIC. References the SAME queue + compute environment + job-task
# role from main.tf — only the image, env, and command differ from grace2-sfincs.
#
# BASELINE sizing 4 vCPU / 8192 MiB (mf6 groundwater solves are lighter than
# SFINCS hydrodynamics). The agent's _run_solver_aws_batch overrides vcpu/memory
# PER JOB via containerOverrides, so the baseline here is just a fallback.
#
# Environment:
#   GRACE2_OBJECT_STORE=s3   — the scheme-aware entrypoint routes object I/O
#                              through boto3 (s3://).
#   GRACE2_RUNS_BUCKET       — the runs bucket; also overridden per-job by the
#                              agent (belt-and-suspenders, entrypoint parity).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_definition" "modflow" {
  name = "grace2-modflow"
  type = "container"

  container_properties = jsonencode({
    # The mf6/FloPy worker image (grace2-modflow ECR repo, :latest tag). Batch
    # pulls the tag on each job start; use digest pinning for production repro.
    image = "${aws_ecr_repository.modflow.repository_url}:latest"

    # Baseline resource allocation — the agent overrides these per-job.
    #   small:     4 vCPU /  8192 MiB  (default for mf6)
    #   standard:  8 vCPU / 16384 MiB
    #   large:    16 vCPU / 32768 MiB
    resourceRequirements = [
      { type = "VCPU", value = "4" },
      { type = "MEMORY", value = "8192" },
    ]

    # Reuse the SAME job-task role main.tf created (S3 runs+cache access + ECS
    # execution). Engine-agnostic — no MODFLOW-specific IAM is required.
    jobRoleArn       = aws_iam_role.job_task.arn
    executionRoleArn = aws_iam_role.job_task.arn

    environment = [
      { name = "GRACE2_OBJECT_STORE", value = "s3" },
      { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "AWS_REGION", value = var.region },
    ]

    # Placeholder command — overridden per-job by the agent's containerOverrides
    # with ["--run-id", "<run_id>", "--manifest-uri", "<s3_uri>"]. The entrypoint
    # requires --run-id, so the placeholder returns a clean non-zero at
    # registration (Batch only needs a non-empty command array).
    command = ["--run-id", "placeholder", "--manifest-uri", "placeholder"]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "modflow"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  # Timeout: 3600 s (1 hour) — same headroom as the SFINCS/SWMM job-defs.
  timeout {
    attempt_duration_seconds = 3600
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

output "job_definition_name_modflow" {
  description = "Name of the MODFLOW Batch job definition. Set GRACE2_AWS_BATCH_JOB_DEF_MODFLOW to this on the agent box."
  value       = aws_batch_job_definition.modflow.name
}

output "ecr_repository_url_modflow" {
  description = "Full ECR repository URL for the MODFLOW worker image. Use this as the image tag base when building/pushing services/workers/modflow/Dockerfile."
  value       = aws_ecr_repository.modflow.repository_url
}
