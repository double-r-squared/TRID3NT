# GRACE-2 off-box worker-image builder (AWS CodeBuild).
#
# WHY: container images were being built ON the auto-stopping agent box
# (i-0251879a278df797f). That is wrong on two counts:
#   1. the scale-to-zero autostop idle-check is BLIND to docker builds (it only
#      sees active agent turns + in-flight Batch jobs), so a >15-min build gets
#      killed mid-flight when the box looks idle (this repeatedly killed the
#      geoclaw source-clawpack + openquake rebuilds).
#   2. builds compete with the agent serving live users.
#
# THIS: a single reusable CodeBuild project that builds ANY worker image off-box,
# isolated, scale-to-zero (pay per build-minute), on a LARGE compute type (8 vCPU
# /15 GB) -- much faster than the downsized agent box, esp. for the GeoClaw
# Fortran compile. Parameterized per build via WORKER_DIR + ECR_REPO env-var
# overrides; the build context is the same engine_workers_src.tgz the orchestrator
# already uploads to the agent-bundle bucket.
#
# Trigger:
#   aws codebuild start-build --project-name grace2-worker-builder --region us-west-2 \
#     --environment-variables-override \
#       name=WORKER_DIR,value=geoclaw,type=PLAINTEXT \
#       name=ECR_REPO,value=grace2-geoclaw,type=PLAINTEXT

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.0" }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "account_id" {
  type    = string
  default = "226996537797"
}

variable "source_bucket" {
  type        = string
  description = "Bucket holding engine_workers_src.tgz (the build context)."
  default     = "grace2-agent-bundle-226996537797"
}

variable "compute_type" {
  type        = string
  description = "CodeBuild compute size. LARGE = 8 vCPU / 15 GB (fast worker builds)."
  default     = "BUILD_GENERAL1_LARGE"
}

locals {
  registry = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

# --------------------------------------------------------------------------- #
# IAM role for the build: ECR push + read the source bucket + CW logs only.
# --------------------------------------------------------------------------- #
resource "aws_iam_role" "builder" {
  name = "grace2-worker-builder"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "builder" {
  name = "grace2-worker-builder-policy"
  role = aws_iam_role.builder.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer", "ecr:PutImage",
          "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload"
        ]
        Resource = "arn:aws:ecr:${var.region}:${var.account_id}:repository/grace2-*"
      },
      {
        Sid      = "SourceRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.source_bucket}", "arn:aws:s3:::${var.source_bucket}/*"]
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/codebuild/grace2-worker-builder*"
      }
    ]
  })
}

# --------------------------------------------------------------------------- #
# The reusable build project. NO_SOURCE: the buildspec pulls the context tarball
# from S3 itself, so we reuse the orchestrator's existing upload flow. Docker via
# privileged_mode. WORKER_DIR + ECR_REPO are defaulted here and overridden per
# build with --environment-variables-override.
# --------------------------------------------------------------------------- #
resource "aws_codebuild_project" "builder" {
  name          = "grace2-worker-builder"
  description   = "Off-box builder for GRACE-2 worker images (isolated, scale-to-zero, LARGE compute)."
  service_role  = aws_iam_role.builder.arn
  build_timeout = 40

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = var.compute_type
    image           = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    privileged_mode = true # required for docker build

    environment_variable {
      name  = "REGISTRY"
      value = local.registry
    }
    environment_variable {
      name  = "AWS_REGION"
      value = var.region
    }
    environment_variable {
      name  = "SRC_S3"
      value = "s3://${var.source_bucket}/engine-build/engine_workers_src.tgz"
    }
    environment_variable {
      name  = "WORKER_DIR"
      value = "sfincs_deckbuilder"
    }
    environment_variable {
      name  = "ECR_REPO"
      value = "grace2-sfincs-quadtree"
    }
  }

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
            "aws s3 cp $SRC_S3 /tmp/src.tgz",
            "tar xzf /tmp/src.tgz -C ctx",
            "ls ctx/services/workers"
          ]
        }
        build = {
          commands = [
            "cd ctx",
            "echo Building WORKER_DIR=$WORKER_DIR -> ECR_REPO=$ECR_REPO",
            "docker build --file services/workers/$WORKER_DIR/Dockerfile --tag $REGISTRY/$ECR_REPO:latest --tag $REGISTRY/$ECR_REPO:codebuild .",
            "docker push $REGISTRY/$ECR_REPO:latest",
            "docker push $REGISTRY/$ECR_REPO:codebuild",
            "echo PUSH_OK $ECR_REPO"
          ]
        }
      }
    })
  }

  logs_config {
    cloudwatch_logs { group_name = "/aws/codebuild/grace2-worker-builder" }
  }
}

output "project_name" {
  value = aws_codebuild_project.builder.name
}
