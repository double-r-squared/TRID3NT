# geoclaw.tf — GeoClaw (Clawpack) AWS Batch job definition + ECR repo (sprint-17).
#
# GeoClaw (Clawpack shallow-water solver) is a NEW Batch user (after SFINCS, SWMM,
# MODFLOW). Per DESIGN INVARIANT 2 in main.tf ("the compute environment and queue
# are ENGINE-AGNOSTIC ... each gets its own job definition on the SAME compute
# environment and queue"), this file adds ONLY the GeoClaw-scoped resources,
# mirroring swmm.tf / modflow.tf exactly:
#
#   - a grace2-geoclaw ECR repository (the Clawpack worker image —
#     services/workers/geoclaw/Dockerfile — is pushed here)
#   - an aws_batch_job_definition.geoclaw pointing at that image, on the SAME
#     grace2-solvers queue + grace2-solvers-spot compute environment + the SAME
#     grace2-batch-job-task-role IAM (all defined in main.tf)
#
# Additive only (ECR repo + lifecycle + job def). The image is built+pushed
# separately ON the agent EC2 box (linux/amd64 — the Clawpack Fortran compiles
# for x86_64) per RUNBOOK, and the agent stays inert until
# GRACE2_AWS_BATCH_JOB_DEF_GEOCLAW is set on the box (registering a job def that
# references an unpushed tag is harmless — Batch resolves the image at job start,
# not at registration). GeoClaw's run_geoclaw.register_geoclaw_solver requires a
# GeoClaw-OWN job-def (it routes via GRACE2_AWS_BATCH_JOB_DEF_GEOCLAW), so GeoClaw
# never cross-routes into the SFINCS image off the generic GRACE2_AWS_BATCH_JOB_DEF.
#
# Per-solver routing seam: after apply + push, set
# GRACE2_AWS_BATCH_JOB_DEF_GEOCLAW = job_definition_name_geoclaw (output below) on
# the agent EC2 box; SFINCS/SWMM/MODFLOW routing is unchanged.

variable "geoclaw_ecr_repo_name" {
  type        = string
  description = "Name for the ECR repository that will hold the GeoClaw (Clawpack) worker image. GeoClaw is a new Batch user; its own image lives in its own repo."
  default     = "grace2-geoclaw"
}

# ─────────────────────────────────────────────────────────────────────────────
# ECR REPOSITORY — grace2-geoclaw
# Mirror of aws_ecr_repository.swmm/modflow (scan-on-push + a 10-image lifecycle cap).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "geoclaw" {
  name                 = var.geoclaw_ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # NOTE: AWS ECR tag VALUES reject parentheses (allowed: letters, digits,
  # spaces, and + - = . _ : / @). Keep "Clawpack" unparenthesized or
  # CreateRepository fails with InvalidTagParameterException.
  tags = {
    description = "GeoClaw Clawpack shallow-water solver worker image - services/workers/geoclaw/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "geoclaw" {
  repository = aws_ecr_repository.geoclaw.name

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
# BATCH JOB DEFINITION — grace2-geoclaw
#
# GeoClaw-SPECIFIC. References the SAME queue + compute environment + job-task
# role from main.tf — only the image, env, and command differ from grace2-sfincs.
#
# BASELINE sizing 8 vCPU / 16384 MiB (the "standard" compute class). GeoClaw's
# AMR shallow-water solve parallelizes over grid patches; the agent's
# _run_solver_aws_batch overrides vcpu/memory PER JOB via containerOverrides, so
# the baseline here is just a fallback / documentation.
#
# Environment:
#   GRACE2_OBJECT_STORE=s3   — the scheme-aware entrypoint routes object I/O
#                              through boto3 (s3://).
#   GRACE2_RUNS_BUCKET       — the runs bucket; also overridden per-job by the
#                              agent (belt-and-suspenders, entrypoint parity).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_definition" "geoclaw" {
  name = "grace2-geoclaw"
  type = "container"

  container_properties = jsonencode({
    # The Clawpack worker image (grace2-geoclaw ECR repo, :latest tag). Batch
    # pulls the tag on each job start; use digest pinning for production repro.
    image = "${aws_ecr_repository.geoclaw.repository_url}:latest"

    # Baseline resource allocation — the agent overrides these per-job.
    #   small:     4 vCPU /  8192 MiB
    #   standard:  8 vCPU / 16384 MiB  (default)
    #   large:    16 vCPU / 32768 MiB
    #   xlarge:   48 vCPU / 98304 MiB
    resourceRequirements = [
      { type = "VCPU", value = "8" },
      { type = "MEMORY", value = "16384" },
    ]

    # Reuse the SAME job-task role main.tf created (S3 runs+cache access + ECS
    # execution). Engine-agnostic — no GeoClaw-specific IAM is required.
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
        "awslogs-stream-prefix" = "geoclaw"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  # Timeout: 7200 s (2 hours) — GeoClaw AMR shallow-water solves over a large
  # tsunami/dam-break domain can run longer than the 1h SFINCS/SWMM/MODFLOW
  # budget; the agent's per-job timeout still bounds it tighter when needed.
  timeout {
    attempt_duration_seconds = 7200
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

output "job_definition_name_geoclaw" {
  description = "Name of the GeoClaw Batch job definition. Set GRACE2_AWS_BATCH_JOB_DEF_GEOCLAW to this on the agent box."
  value       = aws_batch_job_definition.geoclaw.name
}

output "ecr_repository_url_geoclaw" {
  description = "Full ECR repository URL for the GeoClaw worker image. Use this as the image tag base when building/pushing services/workers/geoclaw/Dockerfile."
  value       = aws_ecr_repository.geoclaw.repository_url
}
