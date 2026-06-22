# codebuild.tf -- the OFF-BOX builder for the agent image (services/agent/
# Dockerfile), mirroring infra/aws-codebuild (grace2-worker-builder).
#
# WHY (project_offbox_codebuild_worker_builder): the dev box has no docker socket
# and the agent box autostop kills >15-min docker builds. So the agent image
# builds on CodeBuild (isolated, scale-to-zero, LARGE compute). The inline
# buildspec here is the tofu equivalent of buildspec.agent.yml; either runs the
# identical steps. The ECR repo grace2-agent is referenced (assumed pre-created,
# like the worker repos); a TODO(live) calls out creating it if absent.

resource "aws_ecr_repository" "agent" {
  name                 = "grace2-agent"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "grace2-agent" }
}

# Keep only a few image versions -- the agent image is heavy (~1.4 GB).
resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 7 days."
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 7
      }
      action = { type = "expire" }
    }]
  })
}

# --------------------------------------------------------------------------- #
# CodeBuild service role: ECR push to grace2-agent, pull the build context from
# the agent-bundle bucket, write CodeBuild logs.
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "agent_builder_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent_builder" {
  name               = "grace2-agent-builder"
  assume_role_policy = data.aws_iam_policy_document.agent_builder_assume.json
  tags               = { Name = "grace2-agent-builder" }
}

data "aws_iam_policy_document" "agent_builder" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.agent.arn]
  }
  statement {
    sid       = "ContextRead"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.runs_bucket}/*", "arn:aws:s3:::grace2-agent-bundle-${var.account_id}/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${local.reg}:${local.acct}:log-group:/aws/codebuild/grace2-agent-builder:*"]
  }
}

resource "aws_iam_role_policy" "agent_builder" {
  name   = "grace2-agent-builder-policy"
  role   = aws_iam_role.agent_builder.id
  policy = data.aws_iam_policy_document.agent_builder.json
}

resource "aws_codebuild_project" "agent_builder" {
  name          = "grace2-agent-builder"
  description   = "Off-box builder for the GRACE-2 agent image (Fargate-per-session)."
  service_role  = aws_iam_role.agent_builder.arn
  build_timeout = 40

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = "BUILD_GENERAL1_LARGE"
    image           = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    privileged_mode = true # required for docker build

    environment_variable {
      name  = "REGISTRY"
      value = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com"
    }
    environment_variable {
      name  = "AWS_REGION"
      value = var.region
    }
    environment_variable {
      name  = "ECR_REPO"
      value = "grace2-agent"
    }
    environment_variable {
      name  = "SRC_S3"
      value = "s3://grace2-agent-bundle-${var.account_id}/agent-build/agent_src.tgz"
    }
  }

  # Inline equivalent of buildspec.agent.yml (kept in lock-step with that file).
  source {
    type = "NO_SOURCE"
    buildspec = yamlencode({
      version = "0.2"
      phases = {
        pre_build = {
          commands = [
            "echo Logging in to ECR $REGISTRY",
            "aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $REGISTRY",
            "echo Fetching build context $SRC_S3",
            "rm -rf ctx && mkdir -p ctx",
            "aws s3 cp $SRC_S3 /tmp/agent_src.tgz",
            "tar xzf /tmp/agent_src.tgz -C ctx",
            "ls ctx/services/agent",
          ]
        }
        build = {
          commands = [
            "cd ctx",
            "echo Building agent image -> $REGISTRY/$ECR_REPO",
            "docker build --file services/agent/Dockerfile --tag $REGISTRY/$ECR_REPO:latest --tag $REGISTRY/$ECR_REPO:codebuild .",
            "echo === IMAGE SIZE ===",
            "docker images $REGISTRY/$ECR_REPO:latest",
            "echo === LAYER HISTORY ===",
            "docker history --human --no-trunc $REGISTRY/$ECR_REPO:latest || true",
            "docker push $REGISTRY/$ECR_REPO:latest",
            "docker push $REGISTRY/$ECR_REPO:codebuild",
            "echo PUSH_OK $ECR_REPO",
          ]
        }
      }
    })
  }

  logs_config {
    cloudwatch_logs { group_name = "/aws/codebuild/grace2-agent-builder" }
  }

  tags = { Name = "grace2-agent-builder" }
}
