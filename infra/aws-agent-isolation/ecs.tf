# ecs.tf -- the ECS cluster, the per-session AGENT Fargate task definition, and
# the per-session task security group.
#
# The agent task definition is what the broker `ecs:RunTask`s on a route miss
# (one task == one session == one Python process == real isolation). The
# container env is CARRIED OVER from the live systemd unit (deploy facts) so the
# task runs byte-identical to the box. The broker reaches the task on its private
# IP:8765 (WS) and the reaper polls IP:8766/api/health.
#
# NOTE: there is NO ECS service for the agent task -- it is launched on-demand
# per session by the broker via RunTask, NOT maintained at a desired count. The
# ONLY long-running service here is the broker (broker.tf).

# --------------------------------------------------------------------------- #
# Cluster (shared by the agent tasks + the broker service). Container Insights
# off by default -- cost discipline; flip on if per-task metrics are needed.
# --------------------------------------------------------------------------- #
resource "aws_ecs_cluster" "agents" {
  name = "grace2-agents"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = { Name = "grace2-agents" }
}

# Fargate-only capacity providers (no EC2 backing).
resource "aws_ecs_cluster_capacity_providers" "agents" {
  cluster_name       = aws_ecs_cluster.agents.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# --------------------------------------------------------------------------- #
# Logs for the per-session agent tasks. One log group, one stream prefix per
# task (ECS appends the task id) so the reaper / the orchestrator can read a
# single session's logs in isolation.
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_log_group" "agent" {
  name              = "/grace2/agent-isolation/agent"
  retention_in_days = var.agent_log_retention_days
  tags              = { Name = "grace2-agent-task-logs" }
}

# --------------------------------------------------------------------------- #
# Security group for the per-session agent tasks. Ingress 8765 (WS) + 8766
# (health) ONLY from the broker SG (the broker proxies the WSS + the reaper is
# allowed via the reaper rule). Egress all (Bedrock/Cognito-JWKS/S3/DynamoDB/the
# data sources + Batch submit). NOTHING is public -- the agent task is never
# internet-reachable; only the ALB->broker path is.
# --------------------------------------------------------------------------- #
resource "aws_security_group" "agent_task" {
  name        = "grace2-agent-task"
  description = "Per-session GRACE-2 agent Fargate task. Ingress 8765/8766 from the broker only."
  vpc_id      = var.vpc_id

  egress {
    description = "All egress (Bedrock, Cognito JWKS, S3, DynamoDB, data sources, Batch)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "grace2-agent-task" }
}

# WS 8765 from the broker.
resource "aws_security_group_rule" "agent_ingress_ws_from_broker" {
  type                     = "ingress"
  from_port                = 8765
  to_port                  = 8765
  protocol                 = "tcp"
  security_group_id        = aws_security_group.agent_task.id
  source_security_group_id = aws_security_group.broker.id
  description              = "WS from the broker proxy."
}

# Health 8766 from the broker (and the broker host is also where the reaper's
# probe egress lands if the reaper runs in-VPC; the reaper Lambda variant probes
# via the same SG path -- see reaper.tf for the VPC note).
resource "aws_security_group_rule" "agent_ingress_health_from_broker" {
  type                     = "ingress"
  from_port                = 8766
  to_port                  = 8766
  protocol                 = "tcp"
  security_group_id        = aws_security_group.agent_task.id
  source_security_group_id = aws_security_group.broker.id
  description              = "/api/health from the broker / in-VPC reaper."
}

# --------------------------------------------------------------------------- #
# The per-session AGENT task definition. The container env mirrors the live
# systemd unit (deploy facts); the task ROLE carries exactly what the agent uses
# today (Bedrock, S3 runs/cache, Batch submit/describe, DynamoDB) -- see iam.tf.
# --------------------------------------------------------------------------- #
resource "aws_ecs_task_definition" "agent" {
  family                   = "grace2-agent-session"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.agent_task_cpu
  memory                   = var.agent_task_memory

  # X86_64 (matches the agent EC2 box + the worker images + the amd64 Dockerfile).
  # ARM64 is an AgentCore-LATER concern, explicitly out of scope here.
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  execution_role_arn = aws_iam_role.agent_task_execution.arn
  task_role_arn      = aws_iam_role.agent_task.arn

  container_definitions = jsonencode([
    {
      name      = "agent"
      image     = var.agent_image
      essential = true

      portMappings = [
        { containerPort = 8765, protocol = "tcp" },
        { containerPort = 8766, protocol = "tcp" },
      ]

      # CARRIED OVER from the live systemd grace2-agent unit (deploy facts). The
      # task IAM role supplies AWS creds (Bedrock/S3/DynamoDB/Batch) -- NO keys
      # are ever set here. These match the image's baked defaults; set explicitly
      # so a tfvars/env change never needs an image rebuild.
      environment = [
        { name = "GRACE2_AGENT_HOST", value = "0.0.0.0" },
        { name = "GRACE2_AGENT_PORT", value = "8765" },
        { name = "GRACE2_AGENT_HTTP_PORT", value = "8766" },
        { name = "MODEL_PROVIDER", value = "bedrock" },
        { name = "GRACE2_AWS_REGION", value = var.region },
        { name = "AWS_REGION", value = var.region },
        { name = "GRACE2_PERSISTENCE_BACKEND", value = "dynamodb" },
        { name = "GRACE2_DYNAMO_TABLE_PREFIX", value = "trid3nt_" },
        { name = "GRACE2_STORAGE_BACKEND", value = "s3" },
        { name = "GRACE2_CACHE_BUCKET", value = var.cache_bucket },
        { name = "GRACE2_RUNS_BUCKET", value = var.runs_bucket },
        { name = "GRACE2_COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
        { name = "GRACE2_COGNITO_CLIENT_ID", value = var.cognito_client_id },
        # Match the live box: enforce auth + arm the sync-tool offload so the
        # isolated agent never blocks its own loop. (QGIS-on-box gate defaults
        # off, which is correct -- Fargate cannot docker-run QGIS anyway.)
        { name = "AUTH_REQUIRED", value = "true" },
        { name = "GRACE2_SYNC_TOOL_OFFLOAD", value = "on" },
        # Solver dispatch: the agent defaults GRACE2_SOLVER_BACKEND=aws-batch but
        # run_solver hard-fails (fail-fast, no hang) unless the queue is named.
        # The box sets this out-of-band; the isolation task def must set it too or
        # every flood dies at dispatch with SOLVER_DISPATCH_FAILED. grace2-solvers
        # is the canonical shared queue (infra/aws-batch). (2026-06-30)
        { name = "GRACE2_SOLVER_BACKEND", value = "aws-batch" },
        { name = "GRACE2_AWS_BATCH_QUEUE", value = "grace2-solvers" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "agent"
        }
      }

      # Container-level health (the AUTHORITATIVE session-busy signal stays the
      # reaper's /api/health poll; this only restarts a wedged process).
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('GRACE2_AGENT_HTTP_PORT','8766')+'/api/health', timeout=4)\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 120
      }
    }
  ])

  tags = { Name = "grace2-agent-session" }
}
