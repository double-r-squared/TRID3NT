# reaper.tf -- the per-task idle reaper Lambda (generalizes infra/aws-autostop's
# single-box idle_check from ec2:StopInstances to ecs:StopTask, per session).
#
# Runs on an EventBridge schedule; for each live route in grace2_session_routes
# it reads the agent's self-reported hb_* heartbeat fields (REAPER_HEALTH_MODE=
# heartbeat, Phase 1 2026-07-06) and StopTasks (+ deletes the route) after
# IDLE_THRESHOLD_CHECKS consecutive not-busy ticks, keeping the per-session
# Batch guard and the Stage-3 idle-open-tab rule. See lambda/task_reaper/handler.py.
#
# VPC: NONE. Heartbeat mode needs only DynamoDB + the public ECS/Batch APIs, so
# the Lambda runs outside the VPC (which let Phase 1 delete the ~$29/mo ECS+Batch
# interface endpoints). Probe mode (in-VPC /api/health on private ENI IPs) is the
# documented rollback -- see the commented vpc_config block below.

# --------------------------------------------------------------------------- #
# Package the handler (no third-party deps -- boto3 + urllib are in the runtime).
# --------------------------------------------------------------------------- #
data "archive_file" "reaper_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/task_reaper"
  output_path = "${path.module}/build/task_reaper.zip"
  excludes    = ["tests", "tests/*", "__pycache__", "__pycache__/*"]
}

# aws_security_group.reaper + aws_security_group_rule.agent_ingress_health_from_reaper:
# DESTROYED 2026-07-06 (Phase 1). The reaper is heartbeat-mode and non-VPC -- it
# never probes 8766, so both the Lambda SG and the agent-SG ingress allow are dead.
# Removed from code so an apply cannot recreate them. Rollback to probe mode
# requires: interface endpoints (vpc_endpoints.tf) + these two resources +
# vpc_config below -- all in git history at c776178^..HEAD.

# --------------------------------------------------------------------------- #
# Reaper IAM: ecs:DescribeTasks/StopTask/ListTasks, dynamodb on the routes table,
# batch describe (G3), VPC ENI management, and basic logs.
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "reaper_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "reaper" {
  name               = "grace2-agent-task-reaper"
  assume_role_policy = data.aws_iam_policy_document.reaper_assume.json
  tags               = { Name = "grace2-agent-task-reaper" }
}

# VPC Lambda needs ENI management (AWS-managed policy).
resource "aws_iam_role_policy_attachment" "reaper_vpc" {
  role       = aws_iam_role.reaper.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "reaper" {
  statement {
    sid       = "EcsStopDescribe"
    actions   = ["ecs:StopTask", "ecs:DescribeTasks"]
    resources = ["arn:aws:ecs:${local.reg}:${local.acct}:task/${aws_ecs_cluster.agents.name}/*"]
  }
  statement {
    sid       = "EcsListTasks"
    actions   = ["ecs:ListTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.agents.arn]
    }
  }
  statement {
    sid = "RoutesTableRW"
    actions = [
      "dynamodb:Scan",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
    ]
    resources = [aws_dynamodb_table.session_routes.arn]
  }
  statement {
    sid       = "BatchDescribe"
    actions   = ["batch:ListJobs"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "reaper" {
  name   = "grace2-agent-task-reaper-policy"
  role   = aws_iam_role.reaper.id
  policy = data.aws_iam_policy_document.reaper.json
}

# --------------------------------------------------------------------------- #
# The Lambda.
# --------------------------------------------------------------------------- #
resource "aws_lambda_function" "reaper" {
  function_name = "grace2-agent-task-reaper"
  role          = aws_iam_role.reaper.arn
  handler       = "handler.handler"
  runtime       = "python3.13"
  timeout       = 60
  memory_size   = 256

  filename         = data.archive_file.reaper_zip.output_path
  source_code_hash = data.archive_file.reaper_zip.output_base64sha256

  # Phase-1 scale-to-zero (2026-07-06): vpc_config REMOVED. The reaper now runs
  # heartbeat mode (REAPER_HEALTH_MODE=heartbeat) reading hb_* route-row fields
  # from DynamoDB, so it no longer probes agent private IPs and needs no VPC
  # attachment; ECS/Batch calls reach the public API endpoints from the Lambda
  # service network. Rollback: restore this block + reaper_health_mode=probe.
  #   vpc_config {
  #     subnet_ids         = var.task_subnet_ids
  #     security_group_ids = [aws_security_group.reaper.id]
  #   }

  environment {
    variables = {
      ECS_CLUSTER           = aws_ecs_cluster.agents.name
      ROUTES_TABLE          = var.routes_table_name
      AGENT_HEALTH_PORT     = "8766"
      IDLE_THRESHOLD_CHECKS = tostring(var.idle_threshold_checks)
      BATCH_QUEUES          = var.batch_queues
      HEALTH_TIMEOUT_S      = tostring(var.reaper_health_timeout_s)
      ROUTE_TTL_SECONDS     = tostring(var.route_ttl_seconds)
      DRY_RUN               = var.reaper_dry_run ? "true" : "false"
      # Orphan + max-age reaping (the leak fix): enumerate RUNNING tasks of THIS
      # family directly and stop any that no live route backs (past the grace) or
      # that are simply too old. AGENT_TASK_FAMILY is pinned to the task-def
      # resource so it can never drift from the family the broker RunTasks.
      AGENT_TASK_FAMILY    = aws_ecs_task_definition.agent.family
      ORPHAN_GRACE_SECONDS = tostring(var.reaper_orphan_grace_seconds)
      MAX_AGE_SECONDS      = tostring(var.reaper_max_age_seconds)
      # Phase-1 scale-to-zero (design 2.3): heartbeat mode.
      # "probe" (default) = today's VPC-attached HTTP-probe behavior (no change).
      # "heartbeat" = read hb_* from route row; no VPC probe; per-session Batch guard.
      # "both" = run probe + heartbeat; log agreement; act on probe (safe migration).
      REAPER_HEALTH_MODE      = var.reaper_health_mode
      HEARTBEAT_STALE_SECONDS = tostring(var.reaper_heartbeat_stale_seconds)
    }
  }

  # Phase-1 scale-to-zero (design 2.3): VPC attachment.
  #
  # In probe/both mode the vpc_config below is REQUIRED (the reaper must reach
  # each task's private :8766). In heartbeat-ONLY mode it is unnecessary --
  # the reaper reads only DynamoDB (a free Gateway endpoint) and the ECS/Batch
  # control-plane APIs (reachable from the public internet or via the existing
  # Gateway endpoint if one is added for S3, but NOT requiring the expensive
  # ECS + Batch Interface endpoints).
  #
  # OPERATOR CUT-OVER SEQUENCE (after validating "both" mode agrees):
  #   1. Set var.reaper_health_mode = "heartbeat" -> tofu apply.
  #   2. Remove the vpc_config block below (or set to empty) -> tofu apply.
  #   3. Destroy aws_vpc_endpoint.ecs + aws_vpc_endpoint.batch in vpc_endpoints.tf.
  #   4. Remove the reaper SG egress rule to agent tasks on 8766 (no longer needed).
  #   TODO(operator): flip var.reaper_health_mode and remove this vpc_config once
  #   "both" mode shows consistent agreement over >=2 reaper cycles in CloudWatch.

  tags = { Name = "grace2-agent-task-reaper" }
}

# --------------------------------------------------------------------------- #
# EventBridge schedule -> the reaper.
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_event_rule" "reaper_schedule" {
  name                = "grace2-agent-task-reaper-schedule"
  description         = "Periodic per-session Fargate task idle reaper."
  schedule_expression = var.reaper_schedule_expression
}

resource "aws_cloudwatch_event_target" "reaper_target" {
  rule = aws_cloudwatch_event_rule.reaper_schedule.name
  arn  = aws_lambda_function.reaper.arn
}

resource "aws_lambda_permission" "reaper_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reaper.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reaper_schedule.arn
}
