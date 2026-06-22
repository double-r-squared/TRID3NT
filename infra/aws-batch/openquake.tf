# openquake.tf — OpenQuake Engine PSHA AWS Batch job definition + ECR repo
# (sprint-17).
#
# OpenQuake is a NEW non-SFINCS Batch user (the multi-hazard workbench's seismic
# driver, pairing with the existing Pelicun impact path). Per DESIGN INVARIANT 2
# in main.tf ("the compute environment and queue are ENGINE-AGNOSTIC ... each
# gets its own job definition on the SAME compute environment and queue"), this
# file adds ONLY the OpenQuake-scoped resources, mirroring swmm.tf / modflow.tf
# exactly:
#
#   - a grace2-openquake ECR repository (the OpenQuake worker image —
#     services/workers/openquake/Dockerfile — is pushed here)
#   - an aws_batch_job_definition.openquake pointing at that image, on the SAME
#     grace2-solvers queue + grace2-solvers-spot compute environment + the SAME
#     grace2-batch-job-task-role IAM (all defined in main.tf)
#
# Additive only (ECR repo + lifecycle + job def). NOT applied — authored for NATE
# to `tofu apply`. The image is pushed separately and the agent stays inert until
# GRACE2_AWS_BATCH_JOB_DEF_OPENQUAKE is set on the box (registering a job def that
# references an unpushed tag is harmless — Batch resolves the image at job start).
#
# Per-solver routing seam: after apply + push, set
# GRACE2_AWS_BATCH_JOB_DEF_OPENQUAKE = job_definition_name_openquake (output
# below) on the agent EC2 box; SFINCS/SWMM/MODFLOW routing is unchanged.

variable "openquake_ecr_repo_name" {
  type        = string
  description = "Name for the ECR repository that will hold the OpenQuake Engine PSHA worker image. OpenQuake is a new Batch user; its own image lives in its own repo."
  default     = "grace2-openquake"
}

# ─────────────────────────────────────────────────────────────────────────────
# ECR REPOSITORY — grace2-openquake
# Mirror of aws_ecr_repository.modflow (scan-on-push + a 10-image lifecycle cap).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "openquake" {
  name                 = var.openquake_ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # NOTE: AWS ECR tag VALUES reject parentheses (allowed: letters, digits,
  # spaces, and + - = . _ : / @). Keep the description unparenthesized or
  # CreateRepository fails with InvalidTagParameterException.
  tags = {
    description = "OpenQuake Engine PSHA solver worker image - services/workers/openquake/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "openquake" {
  repository = aws_ecr_repository.openquake.name

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
# BATCH JOB DEFINITION — grace2-openquake
#
# OpenQuake-SPECIFIC. References the SAME queue + compute environment + job-task
# role from main.tf — only the image, env, and command differ from grace2-sfincs.
#
# RAM SIZING (load-bearing, OpenQuake-specific): the OpenQuake engine is
# RAM-hungry — roughly ~2 GB per worker thread for a classical PSHA. The baseline
# below pairs 2 vCPU with 8192 MiB (~4 GB/core, comfortably above the ~2 GB/core
# floor so a small demo grid never OOMs) — DELIBERATELY memory-heavy relative to
# the SWMM (8 vCPU / 16384 MiB = 2 GB/core) and MODFLOW (4 vCPU / 8192 MiB =
# 2 GB/core) defaults. The agent's _run_solver_aws_batch overrides vcpu/memory
# PER JOB via containerOverrides for a larger AOI/site-grid (and the worker pins
# OQ_NUM_CORES low so cores*~2GB stays under the job memory).
#
# Environment:
#   GRACE2_OBJECT_STORE=s3   — the scheme-aware entrypoint routes object I/O
#                              through boto3 (s3://).
#   GRACE2_RUNS_BUCKET       — the runs bucket; also overridden per-job by the
#                              agent (belt-and-suspenders, entrypoint parity).
#   OQ_NUM_CORES / OQ_DISTRIBUTE — RAM guard knobs (also baked in the Dockerfile).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_definition" "openquake" {
  name = "grace2-openquake"
  type = "container"

  container_properties = jsonencode({
    # The OpenQuake worker image (grace2-openquake ECR repo, :latest tag). Batch
    # pulls the tag on each job start; use digest pinning for production repro.
    image = "${aws_ecr_repository.openquake.repository_url}:latest"

    # Baseline resource allocation — the agent overrides these per-job. OpenQuake
    # is RAM-hungry (~2 GB/thread), so the baseline is memory-heavy:
    #   standard (default):  2 vCPU /  8192 MiB  (~4 GB/core, demo site grid)
    #   large:               4 vCPU / 16384 MiB
    #   xlarge:              8 vCPU / 32768 MiB
    resourceRequirements = [
      { type = "VCPU", value = "2" },
      { type = "MEMORY", value = "8192" },
    ]

    # Reuse the SAME job-task role main.tf created (S3 runs+cache access + ECS
    # execution). Engine-agnostic — no OpenQuake-specific IAM is required.
    jobRoleArn       = aws_iam_role.job_task.arn
    executionRoleArn = aws_iam_role.job_task.arn

    environment = [
      { name = "GRACE2_OBJECT_STORE", value = "s3" },
      { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "AWS_REGION", value = var.region },
      # RAM guard: cap engine worker cores so cores*~2GB stays under job memory.
      { name = "OQ_NUM_CORES", value = "2" },
      { name = "OQ_DISTRIBUTE", value = "no" },
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
        "awslogs-stream-prefix" = "openquake"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  # Timeout: 3600 s (1 hour) — same headroom as the SFINCS/SWMM/MODFLOW job-defs.
  timeout {
    attempt_duration_seconds = 3600
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

output "job_definition_name_openquake" {
  description = "Name of the OpenQuake Batch job definition. Set GRACE2_AWS_BATCH_JOB_DEF_OPENQUAKE to this on the agent box."
  value       = aws_batch_job_definition.openquake.name
}

output "ecr_repository_url_openquake" {
  description = "Full ECR repository URL for the OpenQuake worker image. Use this as the image tag base when building/pushing services/workers/openquake/Dockerfile."
  value       = aws_ecr_repository.openquake.repository_url
}
