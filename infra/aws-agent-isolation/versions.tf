# versions.tf -- OpenTofu + AWS provider pins for the agent-isolation
# (Fargate-per-session) module.
#
# SELF-CONTAINED root module (mirrors infra/aws-autostop): its own remote state,
# its own provider, NO dependency on the repo-level infra/ root. It reads a few
# live values (subnets/SGs/cert/ECR repo) as variables and CREATES: the
# grace2_session_routes DynamoDB table, the ECS cluster + the agent Fargate task
# definition, the ALB (long idle timeout), the broker ECS service, the per-task
# idle reaper Lambda, and least-privilege IAM.
#
# OpenTofu (MPL-2.0) per PROJECT_STATE decision 2026-06-05 -- all HCL is written
# for `tofu`, not BUSL Terraform. aws ~> 5 matches the rest of the AWS infra.
#
# *** SCAFFOLD -- DO NOT `tofu apply` *** This is STEP 1 (the foundation) of a
# gated migration (RUNBOOK.md). The live single box stays production until a
# later canary + CloudFront /ws cutover the user signs off on. Several variables
# have NO default (subnets/SGs/cert/ECR image) and MUST be filled from live
# values before any plan/apply -- see terraform.tfvars.example + the TODO list in
# RUNBOOK.md.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Remote state in the existing runs bucket (matches infra/aws-autostop +
  # infra/aws-batch). Distinct key so it never collides with the other modules'
  # state. No DynamoDB lock table -- single-operator for now.
  #
  # TODO(live): confirm this bucket/key before `tofu init` -- it is the same
  # runs bucket the other modules use, distinct key.
  backend "s3" {
    bucket = "grace2-hazard-runs-226996537797"
    key    = "tofu-state/aws-agent-isolation.tfstate"
    region = "us-west-2"
  }
}
