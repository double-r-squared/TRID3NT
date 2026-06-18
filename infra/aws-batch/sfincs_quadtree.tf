# sfincs_quadtree.tf — combined coastal quadtree+SnapWave worker: ECR repo +
# Batch job definition (coastal SFINCS North Star).
#
# The COMBINED worker (services/workers/sfincs_deckbuilder/) builds the quadtree+
# SnapWave deck (cht_sfincs) AND solves it (the SnapWave-compiled sfincs binary
# from the deltares/sfincs-cpu:v2.3.3 base) in ONE Batch job — no separate
# deck-build job (the GPL-driven split was reverted; license is irrelevant per
# NATE, and one job avoids a second Spot cold-start). The agent submits this
# job-def for quadtree+SnapWave coastal runs via the per-solver routing
# (GRACE2_AWS_BATCH_JOB_DEF_SFINCS_QUADTREE); regular-grid SFINCS keeps the
# generic grace2-sfincs job-def. Reuses the SAME grace2-solvers queue + CE +
# grace2-batch-job-task-role IAM from main.tf.

# ─────────────────────────────────────────────────────────────────────────────
# ECR REPOSITORY — grace2-sfincs-quadtree
# Mirror of aws_ecr_repository.swmm (scan-on-push + a 10-image lifecycle cap).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "sfincs_quadtree" {
  name                 = "grace2-sfincs-quadtree"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # ECR tag VALUES reject parentheses (allowed: letters, digits, spaces, and
  # + - = . _ : / @) — keep this description unparenthesized.
  tags = {
    description = "Combined cht_sfincs quadtree+SnapWave deck-builder and solver worker - services/workers/sfincs_deckbuilder/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "sfincs_quadtree" {
  repository = aws_ecr_repository.sfincs_quadtree.name

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
# BATCH JOB DEFINITION — grace2-sfincs-quadtree
#
# Combined build+solve. References the SAME queue + CE + job-task role from
# main.tf. Baseline 16 vCPU / 32768 MiB (the deck-build is light but the
# quadtree+SnapWave solve is compute-heavy); the agent's _run_solver_aws_batch
# overrides vcpu/memory PER JOB via containerOverrides, so the baseline is a
# fallback. 2-hour timeout (build + solve in one job; longer than the solve-only
# job-defs).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_definition" "sfincs_quadtree" {
  name = "grace2-sfincs-quadtree"
  type = "container"

  container_properties = jsonencode({
    image = "${aws_ecr_repository.sfincs_quadtree.repository_url}:latest"

    resourceRequirements = [
      { type = "VCPU", value = "16" },
      { type = "MEMORY", value = "32768" },
    ]

    jobRoleArn       = aws_iam_role.job_task.arn
    executionRoleArn = aws_iam_role.job_task.arn

    environment = [
      { name = "GRACE2_OBJECT_STORE", value = "s3" },
      { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "AWS_REGION", value = var.region },
    ]

    # Placeholder command — overridden per-job by the agent's containerOverrides
    # (["--run-id", "<run_id>", "--manifest-uri", "<s3_uri>"]). Batch only needs a
    # non-empty command array at registration time.
    command = ["--run-id", "placeholder", "--manifest-uri", "placeholder"]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "sfincs-quadtree"
      }
    }
  })

  retry_strategy {
    attempts = 1
  }

  timeout {
    attempt_duration_seconds = 7200
  }
}
