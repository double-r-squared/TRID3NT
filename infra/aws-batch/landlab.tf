# landlab.tf — Landlab (CSDMS surface-process) AWS Batch job definition + ECR
# repo (sprint-17 — NEW engine).
#
# Landlab is a NEW Batch user (after SFINCS + SWMM + MODFLOW). Per DESIGN
# INVARIANT 2 in main.tf ("the compute environment and queue are ENGINE-AGNOSTIC
# ... each gets its own job definition on the SAME compute environment and
# queue"), this file adds ONLY the Landlab-scoped resources:
#
#   - a grace2-landlab ECR repository (the Landlab worker image —
#     services/workers/landlab/Dockerfile — is pushed here)
#   - an aws_batch_job_definition.landlab pointing at that image, on the SAME
#     grace2-solvers queue + grace2-solvers-spot compute environment + the SAME
#     grace2-batch-job-task-role IAM (all defined in main.tf)
#
# This file is SELF-CONTAINED (its own local for the repo name + its own output
# blocks) so it does NOT touch the shared variables.tf / outputs.tf that other
# engine lanes also edit (file-disjoint by construction).
#
# WRITTEN but NOT applied in this stage (IMPLEMENT only — no tofu apply). When
# applied (additive resources only — ECR repo + lifecycle + job def), the image
# is NOT pushed yet and the agent stays inert until GRACE2_AWS_BATCH_JOB_DEF_LANDLAB
# is set on the box (registering a job def that references an unpushed tag is
# harmless — Batch resolves the image at job start, not at registration).
#
# Per-solver routing seam (agent side): the agent resolves the job-def PER SOLVER
# via GRACE2_AWS_BATCH_JOB_DEF_<SOLVER> (solver.py::_resolve_batch_job_def).
# After apply, set GRACE2_AWS_BATCH_JOB_DEF_LANDLAB to job_definition_name_landlab
# (output below) on the agent EC2 box; SFINCS/SWMM/MODFLOW keep their own knobs.

locals {
  # ECR tag VALUES reject parentheses; keep this plain.
  landlab_ecr_repo_name = "grace2-landlab"
}

# ─────────────────────────────────────────────────────────────────────────────
# ECR REPOSITORY — grace2-landlab
# Mirror of aws_ecr_repository.swmm (scan-on-push + a 10-image lifecycle cap).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "landlab" {
  name                 = local.landlab_ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    description = "Landlab CSDMS surface-process solver worker image - services/workers/landlab/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "landlab" {
  repository = aws_ecr_repository.landlab.name

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
# BATCH JOB DEFINITION — grace2-landlab
#
# LANDLAB-SPECIFIC. References the SAME queue + compute environment + job-task
# role from main.tf — only the image, env, and command differ from grace2-swmm.
#
# BASELINE sizing 4 vCPU / 8192 MiB. Landlab's LandslideProbability is a
# Monte-Carlo over a RasterModelGrid (vectorized numpy), so it is lighter than
# the SFINCS/SWMM hydrodynamic solvers; the agent's _run_solver_aws_batch
# overrides vcpu/memory PER JOB via containerOverrides, so the baseline here is
# just a fallback / documentation.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_definition" "landlab" {
  name = "grace2-landlab"
  type = "container"

  container_properties = jsonencode({
    # The Landlab worker image (grace2-landlab ECR repo, :latest tag). Batch
    # pulls the tag on each job start; use digest pinning for production.
    image = "${aws_ecr_repository.landlab.repository_url}:latest"

    # Baseline resource allocation — the agent overrides these per-job.
    #   small:     4 vCPU /  8192 MiB  (default for Landlab)
    #   standard:  8 vCPU / 16384 MiB
    #   large:    16 vCPU / 32768 MiB
    resourceRequirements = [
      { type = "VCPU", value = "4" },
      { type = "MEMORY", value = "8192" },
    ]

    # Reuse the SAME job-task role main.tf created (S3 runs+cache access + ECS
    # execution). Engine-agnostic — no Landlab-specific IAM is required.
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
    # requires --run-id, so the placeholder returns exit 2 cleanly (Batch only
    # needs a non-empty command array at registration time).
    command = ["--run-id", "placeholder", "--manifest-uri", "placeholder"]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "landlab"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  # Timeout: 3600 s (1 hour) — same headroom as the SWMM/SFINCS job-defs.
  timeout {
    attempt_duration_seconds = 3600
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUTS (self-contained in this file — do NOT touch the shared outputs.tf).
# ─────────────────────────────────────────────────────────────────────────────

output "job_definition_name_landlab" {
  description = "Name of the Landlab AWS Batch job definition. Set GRACE2_AWS_BATCH_JOB_DEF_LANDLAB to this on the agent EC2 box after apply."
  value       = aws_batch_job_definition.landlab.name
}

output "ecr_repository_url_landlab" {
  description = "Full ECR repository URL for the Landlab worker image. Use this as the image tag base when building and pushing services/workers/landlab/Dockerfile."
  value       = aws_ecr_repository.landlab.repository_url
}
