# broker.tf -- the thin always-on connection broker (ECS service on Fargate).
#
# The broker is the ONLY new long-running compute (everything else is per-session
# ephemeral or shared/unchanged). Per new WSS connection it: verifies the Cognito
# JWT (reusing auth_handshake.cognito_verify -- see broker/cognito_verify.py),
# resolves sub -> internal ULID (users firebase_uid-index GSI), ConsistentReads
# grace2_session_routes(user_ulid, session_id), RunTask + health-waits + writes
# the route on a miss, then bidirectionally proxies the WSS frames task<->client.
# It is STATELESS (all state in DynamoDB), so it scales horizontally behind the
# ALB and a dropped broker just re-resolves the SAME agent task on reconnect.
#
# IMAGE: a separate tiny image (broker/Dockerfile) -- NOT the agent image. The
# scaffold leaves var.broker_image empty; the service is created with a count of
# 0 effect until an image is supplied (a real apply requires broker_image set).

# --------------------------------------------------------------------------- #
# Broker security group. Ingress 8080 from the ALB only. Egress all (Cognito
# JWKS, DynamoDB, ecs:RunTask/Describe via the AWS API, and the proxied WSS to
# each agent task's private IP:8765).
# --------------------------------------------------------------------------- #
# aws_security_group.broker: DESTROYED 2026-07-06 (Phase 2 -- the Fargate broker
# service is retired; the box-hosted broker uses aws_security_group.broker_box in
# broker_on_box.tf). Removed from code so an apply cannot recreate it.

resource "aws_cloudwatch_log_group" "broker" {
  name              = "/grace2/agent-isolation/broker"
  retention_in_days = var.agent_log_retention_days
  tags              = { Name = "grace2-agent-broker-logs" }
}

resource "aws_ecs_task_definition" "broker" {
  family                   = "grace2-agent-broker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.broker_cpu
  memory                   = var.broker_memory

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  execution_role_arn = aws_iam_role.agent_task_execution.arn
  task_role_arn      = aws_iam_role.broker_task.arn

  container_definitions = jsonencode([
    {
      # If broker_image is empty (scaffold), this references a placeholder; a real
      # apply MUST set var.broker_image. Documented as a TODO(live).
      name      = "broker"
      image     = var.broker_image != "" ? var.broker_image : "PLACEHOLDER_SET_broker_image"
      essential = true

      portMappings = [
        { containerPort = 8080, protocol = "tcp" },
      ]

      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "GRACE2_AWS_REGION", value = var.region },
        # The broker reuses the agent's cognito_verify gate -- SAME env names.
        { name = "GRACE2_COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
        { name = "GRACE2_COGNITO_CLIENT_ID", value = var.cognito_client_id },
        # Routing + provisioning targets.
        { name = "ROUTES_TABLE", value = var.routes_table_name },
        { name = "USERS_TABLE", value = var.users_table_name },
        { name = "USERS_FIREBASE_UID_INDEX", value = var.users_firebase_uid_index },
        { name = "ROUTE_TTL_SECONDS", value = tostring(var.route_ttl_seconds) },
        { name = "ECS_CLUSTER", value = aws_ecs_cluster.agents.name },
        { name = "AGENT_TASK_DEFINITION", value = aws_ecs_task_definition.agent.family },
        { name = "AGENT_CONTAINER_NAME", value = "agent" },
        { name = "AGENT_WS_PORT", value = "8765" },
        { name = "AGENT_HEALTH_PORT", value = "8766" },
        # RunTask network config (the agent tasks run in the task subnets w/ the
        # agent SG). Comma-joined lists the broker parses.
        { name = "TASK_SUBNETS", value = join(",", var.task_subnet_ids) },
        { name = "TASK_SECURITY_GROUPS", value = aws_security_group.agent_task.id },
        { name = "BROKER_PORT", value = "8080" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.broker.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "broker"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])

  tags = { Name = "grace2-agent-broker" }
}

# aws_ecs_service.broker: DESTROYED 2026-07-06 (Phase 2). The broker now runs on
# the TiTiler box (broker_on_box.tf); the task DEFINITION above is kept as the
# tested rollback artifact (free), the service + ALB wiring are gone.
