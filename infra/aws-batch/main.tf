# main.tf — SFINCS AWS Batch compute environment + supporting IAM, ECR, and
# security group resources.
#
# DESIGN INVARIANTS (read before modifying):
#
#   1. DATA-SOURCE existing resources; CREATE only new Batch pieces.
#      The VPC, subnets, S3 bucket, and agent EC2 role were hand-provisioned
#      and are NOT managed by this module. Importing them would couple this
#      module's state to resources shared with other infrastructure paths.
#      Instead we read their IDs/ARNs via data sources.
#
#   2. The compute environment and queue are ENGINE-AGNOSTIC.
#      SFINCS gets the first job definition on this substrate. MODFLOW,
#      QGIS-Processing, and the Python sandbox will each get their own job
#      definition on the SAME compute environment and queue. Do NOT add
#      SFINCS-specific assumptions (e.g. hardcoded solver binary paths or
#      model-deck env vars) to the CE or queue resources — those live in
#      job definitions only.
#
#   3. Scale-to-zero is the goal.
#      min_vcpus=0 + desired_vcpus=0. Batch will spin up SPOT instances on
#      demand and drain them when the queue empties. The agent EC2 box stays
#      always-on for WebSocket + Gemini calls but no longer needs compute
#      headroom for SFINCS runs.
#
#   4. SPOT with SPOT_CAPACITY_OPTIMIZED, broad pool, on-demand fallback.
#      SPOT_CAPACITY_OPTIMIZED lets Batch select the SPOT pool with the most
#      available capacity, reducing interruption rates AND placement failures.
#      Its value is proportional to how MANY pools it can choose from, so the
#      SPOT CE's instance pool is broadened to five x86_64 families x four sizes
#      (see var.instance_types) — far more (family x size x AZ) pools than the
#      old single-family list that left a 16-vCPU job RUNNABLE for 70 min.
#      bid_percentage=100 bids up to On-Demand price; Batch still only pays the
#      current SPOT price (typically 60-80% below On-Demand for c7i/m7i). A 100%
#      bid prevents Batch from failing to source capacity when SPOT prices spike.
#
#      SPOT CAPACITY REBALANCING: AWS Batch managed compute environments do NOT
#      expose an EC2 Capacity-Rebalancing toggle through the aws_batch_compute_
#      environment resource (the schema has no such field — Batch owns the
#      internal Spot/EC2 Fleet and sets its own interruption-handling). The
#      provider-supported, in-Batch equivalent IS the SPOT_CAPACITY_OPTIMIZED
#      allocation strategy, which is already enabled here; broadening the pool
#      (above) is the other lever. The hard guarantee that a reclaimed/unplaceable
#      run still FINISHES is the on-demand fallback CE (grace2-solvers-ondemand)
#      attached to the queue at lower priority, not a rebalancing flag.
#
#   5. Public subnets, no NAT.
#      The four us-west-2 subnets in var.subnet_ids have auto-assign public IP
#      enabled (verified in PROJECT_STATE). Batch container instances use the
#      public IP to reach ECR (to pull the image) and S3 (to read/write run
#      objects). No NAT gateway is needed or provisioned here.

# ─────────────────────────────────────────────────────────────────────────────
# DATA SOURCES — existing hand-provisioned resources
# ─────────────────────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

data "aws_vpc" "main" {
  id = var.vpc_id
}

# Read each subnet individually so we can reference them by AZ in outputs if
# needed. For the compute environment we pass var.subnet_ids directly.
data "aws_subnet" "public" {
  for_each = toset(var.subnet_ids)
  id       = each.value
}

data "aws_s3_bucket" "runs" {
  bucket = var.runs_bucket
}

# The cache bucket holds the staged SFINCS deck + manifest.json the solver
# container reads at run start. The task role needs READ-ONLY access to it.
data "aws_s3_bucket" "cache" {
  bucket = var.cache_bucket
}

# The existing IAM role on the agent EC2 instance. We attach an inline policy
# to it below so the agent can submit/describe/terminate Batch jobs and pass
# the roles it needs to create Batch job containers.
data "aws_iam_role" "agent" {
  name = var.agent_role_name
}

# ─────────────────────────────────────────────────────────────────────────────
# ECR REPOSITORY — grace2-sfincs
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "sfincs" {
  name                 = var.ecr_repo_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # Retain cost and security metadata across image rebuilds. The lifecycle
  # policy below caps stored image count to 10.
  tags = {
    description = "SFINCS solver worker image - services/workers/sfincs/Dockerfile"
  }
}

resource "aws_ecr_lifecycle_policy" "sfincs" {
  repository = aws_ecr_repository.sfincs.name

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
# IAM — BATCH SERVICE ROLE
# Allows the AWS Batch control plane to manage EC2 instances, ECS clusters,
# networking, and CloudWatch logs on our behalf.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "batch_service" {
  name        = "grace2-batch-service-role"
  description = "Allows AWS Batch to manage compute resources for GRACE-2 solver jobs."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "batch.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "batch_service_managed" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM — ECS INSTANCE ROLE + PROFILE
# Attached to the EC2 instances Batch launches. Grants the instance the right
# to pull images from ECR, join the ECS cluster Batch manages, and emit logs.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "ecs_instance" {
  name        = "grace2-batch-ecs-instance-role"
  description = "EC2 instance role for GRACE-2 Batch compute instances (ECS agent + ECR pull)."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_instance_ecs" {
  role       = aws_iam_role.ecs_instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "ecs_instance_ecr" {
  role       = aws_iam_role.ecs_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "ecs_instance" {
  name = "grace2-batch-ecs-instance-profile"
  role = aws_iam_role.ecs_instance.name
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM — EC2 SPOT FLEET ROLE
# Required when using SPOT instances in a MANAGED compute environment. Allows
# the Spot Fleet service to request and tag SPOT instances.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "spot_fleet" {
  name        = "grace2-batch-spot-fleet-role"
  description = "Allows Spot Fleet to request and tag EC2 SPOT instances for GRACE-2 Batch."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "spotfleet.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "spot_fleet_managed" {
  role       = aws_iam_role.spot_fleet.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM — JOB TASK ROLE (also used as execution role)
# Assumed by the ECS task (container) running inside each Batch job. Grants:
#   - S3 read/write on the runs bucket (download manifest inputs, upload
#     outputs and completion.json)
#   - ECR image pull is handled by the execution role; reusing this role as
#     the execution role means the task has one fewer role to manage. For
#     tighter least-privilege, split into separate task + execution roles and
#     grant only AmazonECSTaskExecutionRolePolicy to the execution role.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "job_task" {
  name        = "grace2-batch-job-task-role"
  description = "ECS task role for GRACE-2 Batch job containers. Grants S3 runs-bucket access."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "job_task_s3" {
  name = "grace2-batch-job-task-s3"
  role = aws_iam_role.job_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunsBucketReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = "${data.aws_s3_bucket.runs.arn}/*"
      },
      {
        Sid      = "RunsBucketList"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = data.aws_s3_bucket.runs.arn
      },
      {
        # READ on the cache bucket: the container reads the staged SFINCS deck
        # (DEM, precip, landcover, build_spec.json, etc.) from here at run start.
        # The ListBucket grant lets the deck-prefix walk enumerate input objects.
        Sid    = "CacheBucketRead"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
        ]
        Resource = "${data.aws_s3_bucket.cache.arn}/*"
      },
      {
        # WRITE on the cache DECK prefix ONLY: the COMBINED cht_sfincs
        # quadtree+SnapWave worker (services/workers/sfincs_deckbuilder) writes the
        # built deck's manifest.json (and deck artifacts) back to
        # cache/static-30d/sfincs_deck/<deck_id>/ after building. Solve OUTPUTS
        # still go to the runs bucket; this is scoped to the deck prefix only so
        # the worker cannot write anywhere else in the content-addressed cache.
        Sid    = "CacheBucketDeckWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = "${data.aws_s3_bucket.cache.arn}/cache/static-30d/sfincs_deck/*"
      },
      {
        Sid      = "CacheBucketList"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = data.aws_s3_bucket.cache.arn
      }
    ]
  })
}

# Allow the task role to pull images from ECR and emit CloudWatch Logs
# (standard ECS task execution permissions). This doubles as the execution role
# so we also attach the managed execution policy.
resource "aws_iam_role_policy_attachment" "job_task_ecr_exec" {
  role       = aws_iam_role.job_task.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM — INLINE POLICY ON THE EXISTING AGENT EC2 ROLE
# Attached to the hand-provisioned grace2-agent-ec2 role so the agent process
# can submit Batch jobs, poll their status, terminate them on cancellation,
# and pass the task role to the Batch service (iam:PassRole).
#
# NOTE: This creates an inline policy on an EXISTING role that is NOT managed
# by this module (it is a data source). The aws_iam_role_policy resource
# attaches an inline policy; it does not import or take ownership of the role
# itself. Removing this module with `tofu destroy` will remove the inline
# policy but leave the role intact.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role_policy" "agent_batch" {
  name = "grace2-agent-batch-dispatch"
  role = data.aws_iam_role.agent.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # SubmitJob + TerminateJob ARE resource-level actions (they target a
        # specific queue / job-definition / job ARN), so they stay scoped.
        Sid    = "BatchDispatchScoped"
        Effect = "Allow"
        Action = [
          "batch:SubmitJob",
          "batch:TerminateJob",
        ]
        Resource = [
          "arn:aws:batch:${var.region}:${data.aws_caller_identity.current.account_id}:job-queue/*",
          "arn:aws:batch:${var.region}:${data.aws_caller_identity.current.account_id}:job-definition/*",
          "arn:aws:batch:${var.region}:${data.aws_caller_identity.current.account_id}:job/*",
        ]
      },
      {
        # DescribeJobs + ListJobs do NOT support resource-level permissions —
        # AWS evaluates them against Resource "*" and silently denies a scoped
        # ARN. (This was the live bug: the agent's wait_for_completion early-
        # FAILED backstop logged "not authorized ... on resource: *" every poll
        # because these two actions were pinned to job/queue ARNs.) They must be
        # granted on "*".
        Sid    = "BatchDescribeListWildcard"
        Effect = "Allow"
        Action = [
          "batch:DescribeJobs",
          "batch:ListJobs",
        ]
        Resource = "*"
      },
      {
        Sid    = "PassRoleToBatch"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          aws_iam_role.job_task.arn,
        ]
        # Condition: only allow passing to the ECS task service.
        Condition = {
          StringLike = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      }
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# SECURITY GROUP — Batch compute instances
# Egress-only: container instances need outbound access to ECR (HTTPS image
# pull), S3 (HTTPS run objects), and CloudWatch Logs. They accept no inbound
# traffic (Batch jobs are launched by the control plane, not by inbound
# connections).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_security_group" "batch" {
  name        = "grace2-batch-sg"
  description = "GRACE-2 Batch compute instances - egress-only."
  vpc_id      = data.aws_vpc.main.id

  egress {
    description = "Allow all outbound traffic (ECR/S3/CloudWatch HTTPS + any solver upstream data fetches)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # No ingress rules: Batch container instances are not addressed directly.
}

# ─────────────────────────────────────────────────────────────────────────────
# CLOUDWATCH LOG GROUP — shared across all Batch job definitions
# Retaining 30 days of logs keeps debugging data available without incurring
# indefinite storage cost. Adjust retention_in_days per operational preference.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "batch" {
  name              = "/grace2/batch"
  retention_in_days = 30
}

# ─────────────────────────────────────────────────────────────────────────────
# BATCH COMPUTE ENVIRONMENT — grace2-sfincs-spot
#
# ENGINE-AGNOSTIC: this compute environment hosts ALL solver job definitions
# (SFINCS now; MODFLOW/QGIS-Processing/python-sandbox later). Do not add
# SFINCS-specific configuration here.
#
# Scale-to-zero: min_vcpus=0 + desired_vcpus=0. Batch will provision SPOT
# instances only while jobs are queued/running.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_compute_environment" "sfincs_spot" {
  # "grace2-solvers-spot-<suffix>" conveys that this CE hosts all GRACE-2 solver
  # types, not just SFINCS. We use name_PREFIX (not a fixed name) deliberately:
  # several CE attributes (instance_type, the whole compute_resources block) are
  # immutable and force a REPLACEMENT on change. With a fixed name + the default
  # destroy-then-create, AWS Batch would refuse to delete the old CE while the
  # job queue still references it (a queue needs >=1 CE) -> a stuck/degraded
  # apply. name_prefix + create_before_destroy below gives a ZERO-GAP swap: the
  # new CE is created under a fresh name, the queue is repointed to it, THEN the
  # old CE is drained + destroyed. If the new CE fails to create, the old one is
  # left intact (Terraform errors before any destroy) so the solver queue is
  # never left without a CE. Nothing references the CE by fixed name (the agent
  # submits to the QUEUE name "grace2-solvers"); only comments mentioned the old
  # literal.
  compute_environment_name_prefix = "grace2-solvers-spot-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  # The Batch service role must exist before the CE can be created.
  service_role = aws_iam_role.batch_service.arn

  compute_resources {
    type                = "SPOT"
    allocation_strategy = "SPOT_CAPACITY_OPTIMIZED"

    # Scale-to-zero: min=0 means no standing capacity; Batch spins instances up
    # only when a job enters RUNNABLE state. desired=0 is the initial setting
    # (Batch manages it dynamically after the first job submission).
    min_vcpus     = 0
    max_vcpus     = var.max_vcpus
    desired_vcpus = 0

    instance_type = var.instance_types

    # Public subnets — instances get a public IP for ECR/S3 without NAT.
    subnets = var.subnet_ids

    security_group_ids = [aws_security_group.batch.id]

    # The EC2 instance role (via instance profile) allows the ECS agent on each
    # Batch instance to join the managed ECS cluster and pull ECR images.
    instance_role = aws_iam_instance_profile.ecs_instance.arn

    # SPOT fleet role: Batch uses this role to request Spot instances. Required
    # for SPOT type compute environments.
    spot_iam_fleet_role = aws_iam_role.spot_fleet.arn

    # Maximum Spot bid as a percentage of On-Demand price. 100 = bid up to
    # On-Demand price while still paying only the actual Spot market price.
    bid_percentage = var.spot_bid_percentage

    tags = {
      Name = "grace2-batch-spot-instance"
    }
  }

  # Depend on the service role attachment being complete before creating the CE;
  # AWS validates the role's trust policy at CE creation time.
  depends_on = [aws_iam_role_policy_attachment.batch_service_managed]

  # ZERO-GAP REPLACEMENT: immutable compute_resources changes (e.g. the
  # instance_type ladder for the auto compute-class tiers) force a CE
  # replacement. create_before_destroy + the name_prefix above make Batch stand
  # up the new CE, repoint the queue, and only THEN drain + delete the old one —
  # so the solver queue is never left without a compute environment, and a failed
  # create leaves the existing CE untouched.
  lifecycle {
    create_before_destroy = true
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# BATCH COMPUTE ENVIRONMENT — grace2-solvers-ondemand (FALLBACK)
#
# WHY (TRACK SPOT 2026-06-23): long SFINCS runs have failed two ways on the
# SPOT-only substrate — (1) reclaimed mid-run (host terminated, exit -15; a
# reclaimed attempt restarts from element 0, so even with retryStrategy the wall
# clock balloons), and (2) sat RUNNABLE 70 min because no 16-vCPU SPOT capacity
# was placeable. This is an ON-DEMAND compute environment attached to the SAME
# grace2-solvers queue at a LOWER priority (higher order number) than the SPOT
# CE. Batch always tries the highest-priority placeable CE first, so:
#   - normal path: jobs land on SPOT (cost-optimal, the default).
#   - SPOT cannot place (no capacity) OR a SPOT attempt is reclaimed and the
#     retry also cannot find SPOT capacity: Batch falls back to this on-demand
#     CE, guaranteeing the run completes instead of stalling RUNNABLE forever.
#
# COST-SAFE: this CE is ENABLED but scale-to-zero (min/desired = 0), so it costs
# NOTHING until Batch actually has to place a job here; it only runs instances
# when SPOT could not. Its max_vcpus is capped below the SPOT max (var.
# ondemand_max_vcpus default 64 vs SPOT 96) so the fallback cannot fan out as a
# wide on-demand throughput tier — it is a "must finish" safety net only.
#
# type EC2 (on-demand) => NO spot_iam_fleet_role / bid_percentage. We use
# BEST_FIT_PROGRESSIVE so that if the single most-preferred on-demand instance
# type lacks capacity, Batch progresses to additional types from the broadened
# var.ondemand_instance_types pool (mirrors the broadened SPOT pool).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_compute_environment" "sfincs_ondemand" {
  # Same name_prefix + create_before_destroy rationale as the SPOT CE above:
  # compute_resources changes force a REPLACEMENT, and a fixed name would make
  # Batch refuse to delete the old CE while the queue still references it. The
  # prefix gives a zero-gap swap; the agent never references this CE by name
  # (it submits to the QUEUE).
  compute_environment_name_prefix = "grace2-solvers-ondemand-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  service_role = aws_iam_role.batch_service.arn

  compute_resources {
    type = "EC2"
    # BEST_FIT_PROGRESSIVE: try the cheapest fitting on-demand type, and if that
    # type is capacity-constrained, progress to other types in the pool. (SPOT_
    # CAPACITY_OPTIMIZED is SPOT-only and invalid for an EC2/on-demand CE.)
    allocation_strategy = "BEST_FIT_PROGRESSIVE"

    # Scale-to-zero: no standing on-demand capacity. Instances launch ONLY when
    # Batch places a job here (i.e. when SPOT could not take it).
    min_vcpus     = 0
    max_vcpus     = var.ondemand_max_vcpus
    desired_vcpus = 0

    instance_type = var.ondemand_instance_types

    # Same public subnets, security group, and ECS instance role/profile as the
    # SPOT CE — instances reach ECR/S3 via public IP, no NAT.
    subnets            = var.subnet_ids
    security_group_ids = [aws_security_group.batch.id]
    instance_role      = aws_iam_instance_profile.ecs_instance.arn

    # NOTE: no spot_iam_fleet_role and no bid_percentage — those are SPOT-only.

    tags = {
      Name = "grace2-batch-ondemand-instance"
    }
  }

  depends_on = [aws_iam_role_policy_attachment.batch_service_managed]

  lifecycle {
    create_before_destroy = true
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# BATCH JOB QUEUE — grace2-solvers
#
# ENGINE-AGNOSTIC: all solver job definitions (SFINCS, MODFLOW, QGIS, sandbox)
# submit to this queue. If different solver types need different priority tiers,
# add a second queue backed by the same CE rather than creating a new CE.
#
# SPOT-FIRST, ON-DEMAND-FALLBACK ORDERING (TRACK SPOT 2026-06-23): Batch tries
# the LOWEST order number first. order=1 is the SPOT CE (cost-optimal default);
# order=2 is the on-demand CE. A job only lands on on-demand when the SPOT CE
# cannot place it (no capacity) — including on a retry after a SPOT reclamation.
# This keeps SPOT as the default while guaranteeing completion for runs that
# must finish.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_queue" "solvers" {
  name     = "grace2-solvers"
  state    = "ENABLED"
  priority = 1

  # order=1 (highest priority): SPOT — the cost-optimal default.
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.sfincs_spot.arn
  }

  # order=2 (lower priority): ON-DEMAND fallback — used only when SPOT cannot
  # place the job (no capacity / repeated reclamation).
  compute_environment_order {
    order               = 2
    compute_environment = aws_batch_compute_environment.sfincs_ondemand.arn
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# BATCH JOB DEFINITION — grace2-sfincs
#
# SFINCS-SPECIFIC: this job definition is the only SFINCS-scoped resource in
# this module. The container image is the SAME services/workers/sfincs image
# the local-docker backend runs; on AWS Batch it must be pushed to the ECR repo
# created above (see RUNBOOK.md § Step 1 — Build and push the worker image).
#
# BASELINE sizing: 8 vCPU / 16384 MiB (the "standard" compute class per
# solver.py::AWS_BATCH_COMPUTE_CLASS_SIZING). The agent's
# _run_solver_aws_batch() overrides vcpu/memory PER JOB via containerOverrides
# based on the compute_class argument, so the baseline here is just a fallback.
#
# jobRoleArn / executionRoleArn: both point to the same job_task role for
# simplicity (the task role has both s3 access AND ECS execution permissions).
# Split into separate roles if least-privilege hardening is needed later.
#
# Environment variables baked into the definition:
#   GRACE2_OBJECT_STORE=s3   — the scheme-aware entrypoint reads this to route
#                              all object I/O through boto3 (s3://).
#   GRACE2_RUNS_BUCKET       — the runs bucket name; also overridden per-job by
#                              the agent (belt-and-suspenders, entrypoint parity).
#
# NOTE: When MODFLOW/QGIS-Processing/python-sandbox are promoted to Batch, each
# gets its own aws_batch_job_definition resource in this file (or a separate
# <solver>-job-def.tf file), referencing the SAME queue and CE above. The
# container_properties JSON will differ (different image, env, command).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_batch_job_definition" "sfincs" {
  name = "grace2-sfincs"
  type = "container"

  # container_properties is a JSON string (AWS Batch API format, not the
  # ECS task definition format). The agent's submit_job call adds
  # containerOverrides for command, environment, and resourceRequirements on a
  # per-job basis so the baseline values below act as defaults / documentation.
  container_properties = jsonencode({
    # Image: the ECR URL with the :latest tag. After pushing a new image the
    # job definition does NOT need to be re-registered unless container_properties
    # changes — Batch pulls the tag on each job start. Use digest pinning
    # (image@sha256:...) for reproducibility in production.
    image = "${aws_ecr_repository.sfincs.repository_url}:latest"

    # Baseline resource allocation — the agent overrides these per-job.
    #   standard:  8 vCPU / 16384 MiB  (default)
    #   small:     4 vCPU /  8192 MiB
    #   large:    16 vCPU / 32768 MiB
    #   xlarge:   32 vCPU / 65536 MiB
    resourceRequirements = [
      { type = "VCPU", value = "8" },
      { type = "MEMORY", value = "16384" },
    ]

    jobRoleArn       = aws_iam_role.job_task.arn
    executionRoleArn = aws_iam_role.job_task.arn

    # Baked environment — the agent also passes these via containerOverrides for
    # belt-and-suspenders parity with the services/workers/sfincs/entrypoint.py
    # env-var fallback path.
    environment = [
      { name = "GRACE2_OBJECT_STORE", value = "s3" },
      { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "AWS_REGION", value = var.region },
    ]

    # Placeholder command — overridden per-job by the agent's containerOverrides
    # with ["--run-id", "<run_id>", "--manifest-uri", "<s3_uri>"]. The
    # placeholder prevents Batch from rejecting a job definition with an empty
    # command array.
    command = ["--help"]

    # CloudWatch Logs: all container stdout/stderr streams to
    # /grace2/batch/<job_id> log streams in the log group above.
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "sfincs"
      }
    }
  })

  # Batch retries: 0 extra attempts (the agent handles retries at the
  # application layer via solver.py's wait_for_completion error path).
  retry_strategy {
    attempts = 1
  }

  # Timeout: 3600 s (1 hour). NFR-P-4 budgets 15 min for standard class; this
  # leaves 4x headroom for large class runs or slow SPOT acquisition.
  timeout {
    attempt_duration_seconds = 3600
  }
}
