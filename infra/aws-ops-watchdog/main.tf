# main.tf -- grace2-ops-watchdog
#
# Deploys:
#   - SNS topic grace2-ops-alerts + email subscription
#   - IAM role (least-privilege reads + sns:Publish)
#   - Lambda grace2-ops-watchdog (python3.12, 30s, 128MB)
#   - CloudWatch Log Group (30-day retention)
#   - EventBridge rule rate(15 minutes) -> Lambda
#
# Does NOT touch or reference infra/aws-agent-isolation state.
# All live resource ARNs are constructed from locals (account + region).

locals {
  reg  = var.region
  acct = var.account_id

  # ARNs of existing live resources (read-only from this module's perspective)
  ecs_cluster_arn  = "arn:aws:ecs:${local.reg}:${local.acct}:cluster/grace2-agents"
  routes_table_arn = "arn:aws:dynamodb:${local.reg}:${local.acct}:table/grace2_session_routes"
  reaper_fn_arn    = "arn:aws:lambda:${local.reg}:${local.acct}:function:grace2-agent-task-reaper"
}

# --------------------------------------------------------------------------- #
# Lambda package                                                               #
# --------------------------------------------------------------------------- #
data "archive_file" "watchdog_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/watchdog.py"
  output_path = "${path.module}/build/watchdog.zip"
}

# --------------------------------------------------------------------------- #
# SNS topic + email subscription                                               #
# --------------------------------------------------------------------------- #
resource "aws_sns_topic" "ops_alerts" {
  name = "grace2-ops-alerts"
  tags = { Name = "grace2-ops-alerts" }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
  # NOTE: AWS will send a confirmation email to var.alert_email.
  # The subscription is pending until NATE clicks the confirm link.
}

# --------------------------------------------------------------------------- #
# IAM role -- least-privilege (read-only probes + sns:Publish on this topic)  #
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "watchdog_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "watchdog" {
  name               = "grace2-ops-watchdog"
  assume_role_policy = data.aws_iam_policy_document.watchdog_assume.json
  tags               = { Name = "grace2-ops-watchdog" }
}

# AWS managed basic-execution policy (CloudWatch Logs write).
resource "aws_iam_role_policy_attachment" "watchdog_basic_exec" {
  role       = aws_iam_role.watchdog.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "watchdog" {
  # Probe 1a -- list running ECS tasks in the grace2-agents cluster only
  statement {
    sid       = "EcsListTasksClusterScoped"
    actions   = ["ecs:ListTasks"]
    resources = ["*"] # ecs:ListTasks requires Resource=* (no task-ARN support)
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [local.ecs_cluster_arn]
    }
  }

  # Probe 1a -- describe tasks within the cluster (task ARNs from ListTasks)
  statement {
    sid     = "EcsDescribeTasks"
    actions = ["ecs:DescribeTasks"]
    resources = [
      "arn:aws:ecs:${local.reg}:${local.acct}:task/grace2-agents/*",
    ]
  }

  # Probe 1b -- scan session-routes table for live-route count
  statement {
    sid       = "RoutesTableScan"
    actions   = ["dynamodb:Scan"]
    resources = [local.routes_table_arn]
  }

  # Probe 3 -- inspect reaper Lambda config (read DRY_RUN env var)
  statement {
    sid       = "InspectReaperConfig"
    actions   = ["lambda:GetFunctionConfiguration"]
    resources = [local.reaper_fn_arn]
  }

  # Probe 4 -- broker ALB target group discovery + health check
  # DescribeTargetGroups / DescribeTargetHealth do not support resource-level
  # permissions (they require Resource=* per AWS docs).
  statement {
    sid = "AlbDescribeTargets"
    actions = [
      "elasticloadbalancing:DescribeTargetGroups",
      "elasticloadbalancing:DescribeTargetHealth",
    ]
    resources = ["*"]
  }

  # Probe 6 -- list running Batch solver jobs
  # batch:ListJobs does not support resource-level conditions.
  statement {
    sid       = "BatchListJobs"
    actions   = ["batch:ListJobs"]
    resources = ["*"]
  }

  # Probe 7 -- check EC2 fallback box state
  # ec2:DescribeInstances requires Resource=* per AWS docs.
  statement {
    sid       = "Ec2DescribeInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  # Alert publish -- scoped to this topic only
  statement {
    sid       = "SnsPublishAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]
  }
}

resource "aws_iam_role_policy" "watchdog" {
  name   = "grace2-ops-watchdog-policy"
  role   = aws_iam_role.watchdog.id
  policy = data.aws_iam_policy_document.watchdog.json
}

# --------------------------------------------------------------------------- #
# CloudWatch Log Group (explicit so retention is enforced)                    #
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_log_group" "watchdog" {
  name              = "/aws/lambda/grace2-ops-watchdog"
  retention_in_days = var.log_retention_days
  tags              = { Name = "grace2-ops-watchdog" }
}

# --------------------------------------------------------------------------- #
# Lambda function                                                              #
# --------------------------------------------------------------------------- #
resource "aws_lambda_function" "watchdog" {
  function_name = "grace2-ops-watchdog"
  role          = aws_iam_role.watchdog.arn
  handler       = "watchdog.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.watchdog_zip.output_path
  source_code_hash = data.archive_file.watchdog_zip.output_base64sha256

  # No vpc_config -- this Lambda only calls AWS service APIs + one public HTTPS
  # endpoint (CloudFront).  Non-VPC Lambdas have internet egress by default.

  environment {
    variables = {
      SNS_TOPIC_ARN     = aws_sns_topic.ops_alerts.arn
      ECS_CLUSTER       = "grace2-agents"
      ECS_FAMILY        = "grace2-agent-session"
      ROUTES_TABLE      = "grace2_session_routes"
      REAPER_FUNCTION   = "grace2-agent-task-reaper"
      BATCH_QUEUE       = "grace2-solvers"
      AGENT_BOX_ID      = "i-0251879a278df797f"
      CF_URL            = "https://d125yfbyjrpbre.cloudfront.net/"
      ORPHAN_CRIT_MIN   = tostring(var.orphan_crit_min)
      ORPHAN_CRIT_DELTA = tostring(var.orphan_crit_delta)
      ORPHAN_WARN_DELTA = tostring(var.orphan_warn_delta)
      VCPU_QUOTA        = tostring(var.vcpu_quota)
      VCPU_CRIT_PCT     = tostring(var.vcpu_crit_pct)
      BATCH_WARN_JOBS   = tostring(var.batch_warn_jobs)
      CF_TIMEOUT_S      = tostring(var.cf_timeout_s)
    }
  }

  # The log group must exist before the Lambda first writes to it.
  depends_on = [aws_cloudwatch_log_group.watchdog]

  tags = { Name = "grace2-ops-watchdog" }
}

# --------------------------------------------------------------------------- #
# EventBridge schedule -> Lambda                                               #
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_event_rule" "watchdog_schedule" {
  name                = "grace2-ops-watchdog-schedule"
  description         = "Run GRACE-2 ops watchdog on a fixed schedule."
  schedule_expression = var.schedule_rate
  tags                = { Name = "grace2-ops-watchdog" }
}

resource "aws_cloudwatch_event_target" "watchdog_target" {
  rule = aws_cloudwatch_event_rule.watchdog_schedule.name
  arn  = aws_lambda_function.watchdog.arn
}

resource "aws_lambda_permission" "watchdog_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.watchdog.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.watchdog_schedule.arn
}
