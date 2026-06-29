# iam.tf -- least-privilege roles for the agent-isolation module.
#
# THREE roles:
#   1. agent_task_execution  -- the ECS EXECUTION role (shared by agent + broker
#      tasks): pull the ECR image + write CloudWatch Logs. NO app data access.
#   2. agent_task            -- the per-session agent TASK role: EXACTLY what the
#      live agent uses today (Bedrock invoke, S3 runs/cache, Batch submit/describe,
#      DynamoDB on the grace2_ tables). Mirrors the EC2 box role grace2-agent-ec2.
#   3. broker_task           -- the broker TASK role: ecs:RunTask/StopTask/
#      DescribeTasks on the agent task def, dynamodb on the routes table + the
#      users firebase_uid-index GSI, iam:PassRole for the two task roles it
#      launches with. Cognito JWKS is a PUBLIC HTTPS endpoint -> NO IAM.
#
# All resource ARNs are scoped (no Resource="*") wherever the AWS action permits
# it. The few actions that REQUIRE "*" (ecs:RunTask Resource constraints, Bedrock
# foundation-model invoke) are called out inline. This mirrors the discipline of
# the autostop module's scoped instance ARN.

data "aws_caller_identity" "current" {}

locals {
  acct = var.account_id
  reg  = var.region
}

# --------------------------------------------------------------------------- #
# 1. ECS execution role (shared) -- ECR pull + log write only.
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent_task_execution" {
  name               = "grace2-agent-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = { Name = "grace2-agent-task-execution" }
}

# AWS-managed ECS execution policy = ECR pull + CloudWatch Logs create/put.
resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.agent_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --------------------------------------------------------------------------- #
# 2. Per-session AGENT task role -- mirror the live EC2 agent role.
#    (deploy facts: grace2-agent-ec2 carries bedrock-invoke / grace2-cache-write
#     / grace2-runs-write + S3-read; DynamoDB added for the dynamodb backend.)
# --------------------------------------------------------------------------- #
resource "aws_iam_role" "agent_task" {
  name               = "grace2-agent-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = { Name = "grace2-agent-task" }
}

data "aws_iam_policy_document" "agent_task" {
  # Bedrock Converse + cachePoint. InvokeModel(WithResponseStream) on the
  # foundation models the agent uses. Bedrock model invoke is scoped to the
  # foundation-model ARNs (Sonnet/Haiku/Nova) the live agent selects; "*" model
  # wildcard within the region is the documented minimum for cross-model
  # selection. TODO(live): tighten to the exact inference-profile/model ARNs in
  # use if model selection is frozen.
  statement {
    sid     = "BedrockInvoke"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:${local.reg}::foundation-model/*",
      "arn:aws:bedrock:${local.reg}:${local.acct}:inference-profile/*",
    ]
  }

  # S3 runs bucket (read+write) -- run artifacts + the tofu remote state lives
  # here but the agent only touches run keys; scope to the bucket.
  statement {
    sid     = "S3RunsRW"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.runs_bucket}",
      "arn:aws:s3:::${var.runs_bucket}/*",
    ]
  }

  # S3 cache bucket (read+write).
  statement {
    sid     = "S3CacheRW"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.cache_bucket}",
      "arn:aws:s3:::${var.cache_bucket}/*",
    ]
  }

  # AWS Batch: submit + describe solves (the agent dispatches heavy compute to
  # grace2-solvers and polls). Submit/Describe/List/Terminate require Resource=*
  # on the read/list actions; SubmitJob scopes to the job def + queue ARNs.
  # TODO(live): scope SubmitJob to the exact job-def/queue ARNs from infra/aws-batch.
  statement {
    sid = "BatchSubmit"
    actions = [
      "batch:SubmitJob",
    ]
    resources = [
      "arn:aws:batch:${local.reg}:${local.acct}:job-definition/*",
      "arn:aws:batch:${local.reg}:${local.acct}:job-queue/*",
    ]
  }
  statement {
    sid = "BatchDescribe"
    actions = [
      "batch:DescribeJobs",
      "batch:ListJobs",
      "batch:TerminateJob",
    ]
    # Batch Describe/List do not support resource-level scoping -> "*".
    resources = ["*"]
  }

  # DynamoDB on the grace2_ tables (users/cases/chat/secrets/sessions). Scoped to
  # the table-name prefix + their GSIs.
  statement {
    sid = "DynamoGrace2Tables"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      # grace2_* retained for the backup tables + autostop-state; trid3nt_*
      # are the live app tables post-rename (2026-06-29 DynamoDB migration).
      "arn:aws:dynamodb:${local.reg}:${local.acct}:table/grace2_*",
      "arn:aws:dynamodb:${local.reg}:${local.acct}:table/grace2_*/index/*",
      "arn:aws:dynamodb:${local.reg}:${local.acct}:table/trid3nt_*",
      "arn:aws:dynamodb:${local.reg}:${local.acct}:table/trid3nt_*/index/*",
    ]
  }
}

resource "aws_iam_role_policy" "agent_task" {
  name   = "grace2-agent-task-policy"
  role   = aws_iam_role.agent_task.id
  policy = data.aws_iam_policy_document.agent_task.json
}

# --------------------------------------------------------------------------- #
# 3. BROKER task role -- RunTask/StopTask/DescribeTasks + routes table + users
#    GSI + PassRole for the two roles it launches the agent task with.
# --------------------------------------------------------------------------- #
resource "aws_iam_role" "broker_task" {
  name               = "grace2-agent-broker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = { Name = "grace2-agent-broker-task" }
}

data "aws_iam_policy_document" "broker_task" {
  # ECS lifecycle: launch / stop / describe the per-session agent tasks. RunTask
  # is scoped to the agent task-definition family (any revision); Describe/Stop
  # are scoped to tasks in THIS cluster via the cluster condition.
  statement {
    sid       = "EcsRunTask"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${local.reg}:${local.acct}:task-definition/${aws_ecs_task_definition.agent.family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.agents.arn]
    }
  }
  statement {
    sid       = "EcsStopDescribe"
    actions   = ["ecs:StopTask", "ecs:DescribeTasks"]
    resources = ["arn:aws:ecs:${local.reg}:${local.acct}:task/${aws_ecs_cluster.agents.name}/*"]
  }
  # ListTasks does not support resource scoping; constrain by the cluster ARN
  # condition where supported, else "*".
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

  # iam:PassRole for the agent task's execution + task roles (required to RunTask
  # with them). Scoped to exactly those two role ARNs.
  statement {
    sid       = "PassAgentRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.agent_task.arn, aws_iam_role.agent_task_execution.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # The routes table: the broker reads/writes (user_ulid, session_id) rows.
  statement {
    sid = "RoutesTableRW"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.session_routes.arn]
  }

  # The users table firebase_uid-index GSI: the broker resolves sub -> ULID
  # (read). Scoped to the users table + its index.
  statement {
    sid     = "UsersResolveRead"
    actions = ["dynamodb:Query", "dynamodb:GetItem"]
    resources = [
      "arn:aws:dynamodb:${local.reg}:${local.acct}:table/${var.users_table_name}",
      "arn:aws:dynamodb:${local.reg}:${local.acct}:table/${var.users_table_name}/index/*",
    ]
  }

  # First-connect provisioning: the broker mints the users row a brand-new
  # verified sub has no row for yet (the agent's in-band create cannot run until
  # AFTER the broker routes). PutItem (conditional create) + UpdateItem (idempotent
  # upsert). Least-privilege: the users BASE table only -- writes never target a
  # GSI directly, and no other table is writable from the broker role.
  statement {
    sid     = "UsersProvisionWrite"
    actions = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [
      "arn:aws:dynamodb:${local.reg}:${local.acct}:table/${var.users_table_name}",
    ]
  }
}

resource "aws_iam_role_policy" "broker_task" {
  name   = "grace2-agent-broker-task-policy"
  role   = aws_iam_role.broker_task.id
  policy = data.aws_iam_policy_document.broker_task.json
}
