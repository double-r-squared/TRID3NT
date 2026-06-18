# swmm.tf — SWMM (pyswmm) AWS Batch job definition + ECR repo (sprint-16 P7).
#
# SWMM is the FIRST non-SFINCS Batch user. Per DESIGN INVARIANT 2 in main.tf
# ("the compute environment and queue are ENGINE-AGNOSTIC ... each gets its own
# job definition on the SAME compute environment and queue"), this file adds
# ONLY the SWMM-scoped resources:
#
#   - a grace2-swmm ECR repository (the pyswmm worker image —
#     services/workers/swmm/Dockerfile — is pushed here)
#   - an aws_batch_job_definition.swmm pointing at that image, on the SAME
#     grace2-solvers queue + grace2-solvers-spot compute environment + the
#     SAME grace2-batch-job-task-role IAM (all defined in main.tf)
#
# NOT APPLIED — authored for NATE to `tofu apply` (and to push the image first;
# the agent stays inert until GRACE2_AWS_BATCH_JOB_DEF_SWMM is set on the box).
#
# Per-solver routing seam (agent side): the agent resolves the job-def PER
# SOLVER via GRACE2_AWS_BATCH_JOB_DEF_<SOLVER> (solver.py::_resolve_batch_job_def).
# After apply, set GRACE2_AWS_BATCH_JOB_DEF_SWMM to job_definition_name_swmm
# (output below) on the agent EC2 box; SFINCS keeps using the generic
# GRACE2_AWS_BATCH_JOB_DEF unchanged.

# ─────────────────────────────────────────────────────────────────────────────
# ECR REPOSITORY — grace2-swmm
# Mirror of aws_ecr_repository.sfincs (scan-on-push + a 10-image lifecycle cap).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "swmm" {
  name                 = var.swmm_ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    description = "SWMM (pyswmm) solver worker image - services/workers/swmm/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "swmm" {
  repository = aws_ecr_repository.swmm.name

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
# BATCH JOB DEFINITION — grace2-swmm
#
# SWMM-SPECIFIC. References the SAME queue + compute environment + job-task role
# from main.tf — only the image, env, and command differ from grace2-sfincs.
#
# BASELINE sizing 8 vCPU / 16384 MiB (the "standard" compute class). The agent's
# _run_solver_aws_batch overrides vcpu/memory PER JOB via containerOverrides, so
# the baseline here is just a fallback / documentation.
#
# Environment:
#   GRACE2_OBJECT_STORE=s3   — the scheme-aware entrypoint routes object I/O
#                              through boto3 (s3://).
#   GRACE2_RUNS_BUCKET       — the runs bucket; also overridden per-job by the
#                              agent (belt-and-suspenders, entrypoint parity).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_definition" "swmm" {
  name = "grace2-swmm"
  type = "container"

  container_properties = jsonencode({
    # The pyswmm worker image (grace2-swmm ECR repo, :latest tag). Batch pulls
    # the tag on each job start; use digest pinning for production reproducibility.
    image = "${aws_ecr_repository.swmm.repository_url}:latest"

    # Baseline resource allocation — the agent overrides these per-job.
    #   standard:  8 vCPU / 16384 MiB  (default)
    #   small:     4 vCPU /  8192 MiB
    #   large:    16 vCPU / 32768 MiB
    #   xlarge:   48 vCPU / 98304 MiB
    resourceRequirements = [
      { type = "VCPU", value = "8" },
      { type = "MEMORY", value = "16384" },
    ]

    # Reuse the SAME job-task role main.tf created (S3 runs+cache access + ECS
    # execution). Engine-agnostic — no SWMM-specific IAM is required.
    jobRoleArn       = aws_iam_role.job_task.arn
    executionRoleArn = aws_iam_role.job_task.arn

    environment = [
      { name = "GRACE2_OBJECT_STORE", value = "s3" },
      { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "AWS_REGION", value = var.region },
    ]

    # Placeholder command — overridden per-job by the agent's containerOverrides
    # with ["--run-id", "<run_id>", "--manifest-uri", "<s3_uri>"]. The
    # entrypoint requires --run-id, so the placeholder returns exit 2 cleanly
    # (Batch only needs a non-empty command array at registration time).
    command = ["--run-id", "placeholder", "--manifest-uri", "placeholder"]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "swmm"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  # Timeout: 3600 s (1 hour) — same headroom as the SFINCS job-def.
  timeout {
    attempt_duration_seconds = 3600
  }
}
