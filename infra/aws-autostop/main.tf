# main.tf — agent-box auto-stop/wake infrastructure.
#
# DESIGN INVARIANTS (read before modifying):
#
#   1. SCOPE EC2 stop/start to ONE instance ARN.
#      The idle-check Lambda may ec2:StopInstances and the wake Lambda may
#      ec2:StartInstances AND ec2:StopInstances ONLY on var.agent_instance_id.
#      The wake Lambda's StopInstances is the server side of the explicit user
#      "sleep" control; the handler gates it behind a valid Cognito token AND a
#      not-busy /api/health probe, so the IAM grant alone never sleeps a busy box
#      (see invariant 4). ec2:DescribeInstances has no resource-level support
#      (AWS evaluates it on "*"), so it is granted on "*" but is read-only. There
#      is NO TerminateInstances anywhere.
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
#   4. The wake (START) action is unauthenticated + CORS-open by design; the
#      sleep (STOP) action is Cognito-gated + busy-guarded.
#      A WAKE (POST default / action=="wake") can only START one hard-coded
#      instance and stays unauthenticated -- the browser must call it before a
#      session exists; abuse ceiling = the box starts (then idle-check stops it),
#      no data exposure. A SLEEP (POST action=="stop") can only STOP that same
#      one instance and is gated in the handler behind a valid Cognito ID token
#      AND a not-busy /api/health probe (a busy/unreachable box -> 409, no stop),
#      so a stray/anonymous call can never sleep the box mid-turn. Neither action
#      can terminate or touch anything else.

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

# The wake handler's STOP action verifies a Cognito ID token (RS256/JWKS), so it
# needs PyJWT[crypto] + requests beyond the runtime boto3 -- same deps as the
# view-signer. Install them into a package dir + copy the handler, then zip
# (mirrors null_resource.view_sign_build). The WAKE action stays dep-free at
# runtime (boto3 + urllib only); the extra deps load lazily only on a stop.
locals {
  wake_src_dir = "${path.module}/lambda/wake"
  wake_pkg_dir = "${path.module}/build/wake_pkg"
}

resource "null_resource" "wake_build" {
  triggers = {
    handler_sha = filesha256("${local.wake_src_dir}/handler.py")
    deps        = "PyJWT[crypto]==2.9.0 requests==2.32.3"
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      rm -rf "${local.wake_pkg_dir}"
      mkdir -p "${local.wake_pkg_dir}"
      python3 -m pip install \
        --platform manylinux2014_x86_64 \
        --implementation cp \
        --python-version 3.12 \
        --only-binary=:all: \
        --target "${local.wake_pkg_dir}" \
        "PyJWT[crypto]==2.9.0" "requests==2.32.3"
      cp "${local.wake_src_dir}/handler.py" "${local.wake_pkg_dir}/handler.py"
    EOT
  }
}

data "archive_file" "wake" {
  type        = "zip"
  source_dir  = local.wake_pkg_dir
  output_path = "${path.module}/build/wake.zip"
  depends_on  = [null_resource.wake_build]
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
# Least privilege: DescribeInstances (read, "*"), StartInstances + StopInstances
# (BOTH scoped to the instance ARN), CloudWatch Logs. No terminate.
# StopInstances is the server side of the explicit user "sleep" control; the
# wake handler gates it behind a valid Cognito token AND a not-busy /api/health
# probe, so the IAM grant alone never sleeps a busy box.
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
        # StopInstances scoped to the ONE agent instance ARN (same scope as the
        # idle-check role's StopAgentInstanceOnly). No terminate. The user-sleep
        # path in the wake handler is the only caller, gated on Cognito + not-busy.
        Sid      = "StopAgentInstanceOnly"
        Effect   = "Allow"
        Action   = ["ec2:StopInstances"]
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
  # Stop path: JWKS fetch (5s) + health probe (health_timeout_s) + EC2 calls on
  # top; small headroom. Memory bumped to load the cryptography native ext.
  timeout     = 15
  memory_size = 256

  environment {
    variables = {
      AGENT_INSTANCE_ID = var.agent_instance_id
      # Stop guard reuses the idle-check health URL + timeout: the SAME busy
      # signal the auto-stop uses (a busy box -> 409, no StopInstances).
      HEALTH_URL       = var.health_url
      HEALTH_TIMEOUT_S = tostring(var.health_timeout_s)
      # Cognito gate for the stop action (mirrors the view-signer). UNSET pool =>
      # every token fails verify -> stop returns 401 (inert until a pool is
      # wired). The wake action is unaffected.
      GRACE2_COGNITO_USER_POOL_ID = var.cognito_user_pool_id
      GRACE2_COGNITO_CLIENT_ID    = var.cognito_client_id
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
    # `authorization` added for the /case-view-url route: a signed-in browser
    # sends the Cognito ID token in the Authorization header, so the shared
    # preflight must allow it (the /wake route never sends it — harmless there).
    allow_headers = ["content-type", "authorization"]
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

# ─────────────────────────────────────────────────────────────────────────────
# VIEW-SIGNER Lambda — GET /case-view-url (reuses the wake API above).
#
# DESIGN INVARIANTS (read before modifying):
#
#   1. SIGN ONLY. The role can s3:GetObject on the runs bucket's case-views/*
#      prefix and nothing else — no s3:ListBucket, no PutObject, no other
#      prefix, no DynamoDB, no EC2. The agent (a different role) WRITES the
#      snapshots; this Lambda only mints time-boxed GET URLs for them.
#
#   2. AUTH TIERING lives in the handler, not the IAM/route. The handler ports
#      cognito_verify from auth_handshake.py: a verified signed-in owner ->
#      view_signed_ttl_s (12h); anonymous / invalid token / no pool -> anon TTL
#      (15min). The bucket stays private; the pre-signed URL is the only read.
#
#   3. THIRD-PARTY DEPS. The handler needs PyJWT[crypto] + requests beyond the
#      runtime boto3. They are pip-installed into build/view_sign_pkg at apply
#      time (null_resource below) and zipped with the handler. Minimal footprint
#      per container-hygiene: only PyJWT[crypto] (+ its cryptography wheel) and
#      requests are installed; no layer, no extra base.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  view_sign_src_dir = "${path.module}/lambda/view_sign"
  view_sign_pkg_dir = "${path.module}/build/view_sign_pkg"
}

# Install the third-party deps + copy the handler into a package dir, then zip.
# The trigger hashes the handler so a code edit re-runs the install; the deps
# pin keeps the artifact reproducible. manylinux/linux platform wheels are
# requested so the cryptography native extension matches the Lambda runtime
# (python3.12 on Amazon Linux x86_64), not the build host.
resource "null_resource" "view_sign_build" {
  triggers = {
    handler_sha = filesha256("${local.view_sign_src_dir}/handler.py")
    deps        = "PyJWT[crypto]==2.9.0 requests==2.32.3"
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      rm -rf "${local.view_sign_pkg_dir}"
      mkdir -p "${local.view_sign_pkg_dir}"
      python3 -m pip install \
        --platform manylinux2014_x86_64 \
        --implementation cp \
        --python-version 3.12 \
        --only-binary=:all: \
        --target "${local.view_sign_pkg_dir}" \
        "PyJWT[crypto]==2.9.0" "requests==2.32.3"
      cp "${local.view_sign_src_dir}/handler.py" "${local.view_sign_pkg_dir}/handler.py"
    EOT
  }
}

data "archive_file" "view_sign" {
  type        = "zip"
  source_dir  = local.view_sign_pkg_dir
  output_path = "${path.module}/build/view_sign.zip"
  depends_on  = [null_resource.view_sign_build]
}

resource "aws_iam_role" "view_sign" {
  name = "${local.name_prefix}-view-sign-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "view_sign" {
  name = "${local.name_prefix}-view-sign"
  role = aws_iam_role.view_sign.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetObject scoped to the case-views/ prefix of the runs bucket — the
        # only thing the signer needs to mint a pre-signed GET URL. No list,
        # no put, no other prefix.
        Sid      = "SignCaseViewSnapshots"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.runs_bucket}/case-views/*"
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.name_prefix}-view-sign:*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "view_sign" {
  name              = "/aws/lambda/${local.name_prefix}-view-sign"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_lambda_function" "view_sign" {
  function_name    = "${local.name_prefix}-view-sign"
  role             = aws_iam_role.view_sign.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.view_sign.output_path
  source_code_hash = data.archive_file.view_sign.output_base64sha256
  # JWKS fetch (5s) + S3 get_object + presign on top; small headroom.
  timeout     = 15
  memory_size = 256

  environment {
    variables = {
      RUNS_BUCKET                 = var.runs_bucket
      GRACE2_COGNITO_USER_POOL_ID = var.cognito_user_pool_id
      GRACE2_COGNITO_CLIENT_ID    = var.cognito_client_id
      SIGNED_TTL                  = tostring(var.view_signed_ttl_s)
      ANON_TTL                    = tostring(var.view_anon_ttl_s)
    }
  }

  depends_on = [aws_cloudwatch_log_group.view_sign]
}

resource "aws_apigatewayv2_integration" "view_sign" {
  api_id                 = aws_apigatewayv2_api.wake.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.view_sign.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "view_sign" {
  api_id    = aws_apigatewayv2_api.wake.id
  route_key = "GET /case-view-url"
  target    = "integrations/${aws_apigatewayv2_integration.view_sign.id}"
}

resource "aws_lambda_permission" "apigw_invoke_view_sign" {
  statement_id  = "AllowAPIGatewayInvokeViewSign"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.view_sign.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.wake.execution_arn}/*/*"
}
