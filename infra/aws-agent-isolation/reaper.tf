# reaper.tf -- the per-task idle reaper Lambda (generalizes infra/aws-autostop's
# single-box idle_check from ec2:StopInstances to ecs:StopTask, per session).
#
# Runs on an EventBridge schedule; for each live route in grace2_session_routes
# it probes that task's /api/health and StopTasks (+ deletes the route) after
# IDLE_THRESHOLD_CHECKS consecutive not-busy ticks, keeping the G3 Batch guard
# and the Stage-3 idle-open-tab rule. See lambda/task_reaper/handler.py.
#
# VPC: the agent tasks have NO public IP, so the reaper runs IN the task subnets
# with a SG allowed to reach the agent SG on 8766 (the one networking difference
# from the EC2 reaper).

# --------------------------------------------------------------------------- #
# Package the handler (no third-party deps -- boto3 + urllib are in the runtime).
# --------------------------------------------------------------------------- #
data "archive_file" "reaper_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/task_reaper"
  output_path = "${path.module}/build/task_reaper.zip"
  excludes    = ["tests", "tests/*", "__pycache__", "__pycache__/*"]
}

# --------------------------------------------------------------------------- #
# Reaper SG: egress to the agent SG on 8766 (health probe) + the AWS APIs.
# --------------------------------------------------------------------------- #
resource "aws_security_group" "reaper" {
  name        = "grace2-agent-task-reaper"
  description = "Per-task idle reaper Lambda. Egress to agent tasks (8766) + AWS APIs."
  vpc_id      = var.vpc_id

  egress {
    description = "ECS/DynamoDB/Batch APIs + the per-task health probe."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "grace2-agent-task-reaper" }
}

# Allow the reaper SG to reach the agent task SG on 8766.
resource "aws_security_group_rule" "agent_ingress_health_from_reaper" {
  type                     = "ingress"
  from_port                = 8766
  to_port                  = 8766
  protocol                 = "tcp"
  security_group_id        = aws_security_group.agent_task.id
  source_security_group_id = aws_security_group.reaper.id
  description              = "/api/health from the per-task idle reaper Lambda."
}

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

  vpc_config {
    subnet_ids         = var.task_subnet_ids
    security_group_ids = [aws_security_group.reaper.id]
  }

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
    }
  }

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
