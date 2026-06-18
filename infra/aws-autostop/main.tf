# main.tf — agent-box auto-stop/wake infrastructure.
#
# DESIGN INVARIANTS (read before modifying):
#
#   1. SCOPE EC2 stop/start to ONE instance ARN.
#      The idle-check Lambda may ec2:StopInstances and the wake Lambda may
#      ec2:StartInstances ONLY on var.agent_instance_id. ec2:DescribeInstances
#      has no resource-level support (AWS evaluates it on "*"), so it is granted
#      on "*" but is read-only. There is NO TerminateInstances anywhere.
#
#   2. The auto-stop logic lives in the Lambda, not the IAM/schedule.
#      This module wires the schedule -> idle Lambda and the HTTP endpoint ->
#      wake Lambda; the bulletproof "never stop a busy box" decision is in
#      lambda/idle_check/handler.py (consecutive-idle streak in DynamoDB + the
#      health probe + the Batch guard). Tuning lives in variables, not HCL edits.
#
#   3. DATA-SOURCE the agent instance; never manage it here.
#      The EC2 box is hand-provisioned and owned by other infra. We read it via
#      a data source for validation/outputs only. `tofu destroy` on this module
#      removes the schedule/Lambdas/IAM/table/API — never the instance.
#
#   4. The wake endpoint is unauthenticated + CORS-open by design.
#      It can only START one hard-coded instance (never stop/terminate/touch
#      anything else). The browser must call it before a session exists. Abuse
#      ceiling = the box starts (then idle-check stops it); no data exposure.

data "aws_caller_identity" "current" {}

# Read the agent instance for validation + outputs. NOT managed here.
data "aws_instance" "agent" {
  instance_id = var.agent_instance_id
}

locals {
  instance_arn = "arn:aws:ec2:${var.region}:${var.account_id}:instance/${var.agent_instance_id}"
  name_prefix  = "grace2-autostop"
}

# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB — consecutive-idle streak store (single item per instance).
# PAY_PER_REQUEST so it costs ~nothing at this access rate (one get+put / 5 min).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "state" {
  name         = "${local.name_prefix}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "instance_id"

  attribute {
    name = "instance_id"
    type = "S"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda packaging — zip each handler directory. No third-party deps (boto3 +
# urllib are in the Lambda runtime), so a plain source zip suffices.
# ─────────────────────────────────────────────────────────────────────────────

data "archive_file" "idle_check" {
  type        = "zip"
  source_file = "${path.module}/lambda/idle_check/handler.py"
  output_path = "${path.module}/build/idle_check.zip"
}

data "archive_file" "wake" {
  type        = "zip"
  source_file = "${path.module}/lambda/wake/handler.py"
  output_path = "${path.module}/build/wake.zip"
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM — idle-check Lambda role.
# Least privilege: DescribeInstances (read, "*"), StopInstances (scoped to the
# instance ARN), DynamoDB get/put on the streak table, Batch list_jobs (read,
# "*" — list_jobs has no resource-level support), and CloudWatch Logs.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "idle_check" {
  name = "${local.name_prefix}-idle-check-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "idle_check" {
  name = "${local.name_prefix}-idle-check"
  role = aws_iam_role.idle_check.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # StopInstances scoped to the ONE agent instance ARN. No terminate.
        Sid      = "StopAgentInstanceOnly"
        Effect   = "Allow"
        Action   = ["ec2:StopInstances"]
        Resource = local.instance_arn
      },
      {
        # DescribeInstances has no resource-level support (must be "*"). Read-only.
        Sid      = "DescribeInstances"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"
      },
      {
        # Batch list_jobs has no resource-level support (must be "*"). Read-only —
        # the in-flight-solve guard.
        Sid      = "BatchListJobs"
        Effect   = "Allow"
        Action   = ["batch:ListJobs"]
        Resource = "*"
      },
      {
        Sid      = "StreakStore"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.name_prefix}-idle-check:*"
      },
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# IAM — wake Lambda role.
# Least privilege: DescribeInstances (read, "*"), StartInstances (scoped to the
# instance ARN), CloudWatch Logs. No stop, no terminate.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "wake" {
  name = "${local.name_prefix}-wake-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "wake" {
  name = "${local.name_prefix}-wake"
  role = aws_iam_role.wake.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StartAgentInstanceOnly"
        Effect   = "Allow"
        Action   = ["ec2:StartInstances"]
        Resource = local.instance_arn
      },
      {
        Sid      = "DescribeInstances"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.name_prefix}-wake:*"
      },
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch Log groups (explicit so retention is managed, not infinite).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "idle_check" {
  name              = "/aws/lambda/${local.name_prefix}-idle-check"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_cloudwatch_log_group" "wake" {
  name              = "/aws/lambda/${local.name_prefix}-wake"
  retention_in_days = var.lambda_log_retention_days
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda functions.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "idle_check" {
  function_name    = "${local.name_prefix}-idle-check"
  role             = aws_iam_role.idle_check.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.idle_check.output_path
  source_code_hash = data.archive_file.idle_check.output_base64sha256
  # The health probe waits up to health_timeout_s; give headroom for the EC2 +
  # Batch + DynamoDB API calls on top.
  timeout     = 30
  memory_size = 128

  environment {
    variables = {
      AGENT_INSTANCE_ID     = var.agent_instance_id
      HEALTH_URL            = var.health_url
      STATE_TABLE           = aws_dynamodb_table.state.name
      IDLE_THRESHOLD_CHECKS = tostring(var.idle_threshold_checks)
      BATCH_QUEUES          = var.batch_queues
      HEALTH_TIMEOUT_S      = tostring(var.health_timeout_s)
      DRY_RUN               = var.dry_run ? "true" : "false"
    }
  }

  depends_on = [aws_cloudwatch_log_group.idle_check]
}

resource "aws_lambda_function" "wake" {
  function_name    = "${local.name_prefix}-wake"
  role             = aws_iam_role.wake.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.wake.output_path
  source_code_hash = data.archive_file.wake.output_base64sha256
  timeout          = 15
  memory_size      = 128

  environment {
    variables = {
      AGENT_INSTANCE_ID = var.agent_instance_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.wake]
}

# ─────────────────────────────────────────────────────────────────────────────
# EventBridge schedule -> idle-check Lambda.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "idle_check" {
  name                = "${local.name_prefix}-idle-check-schedule"
  description         = "Poll the agent /api/health and stop the box after consecutive idle checks."
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "idle_check" {
  rule      = aws_cloudwatch_event_rule.idle_check.name
  target_id = "idle-check-lambda"
  arn       = aws_lambda_function.idle_check.arn
}

resource "aws_lambda_permission" "events_invoke_idle_check" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.idle_check.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.idle_check.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# API Gateway HTTP API -> wake Lambda.
# A single ANY /wake route + auto-deploy $default stage. CORS is set on the API
# (the wake handler also returns CORS headers so direct invokes/tests match).
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "wake" {
  name          = "${local.name_prefix}-wake-api"
  protocol_type = "HTTP"
  description   = "Wake endpoint for the always-on agent EC2 box (StartInstances)."

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "wake" {
  api_id                 = aws_apigatewayv2_api.wake.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.wake.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "wake" {
  api_id    = aws_apigatewayv2_api.wake.id
  route_key = "ANY /wake"
  target    = "integrations/${aws_apigatewayv2_integration.wake.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.wake.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_invoke_wake" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.wake.function_name
  principal     = "apigateway.amazonaws.com"
  # Restrict to this API's executions (any stage/method/route under it).
  source_arn = "${aws_apigatewayv2_api.wake.execution_arn}/*/*"
}
