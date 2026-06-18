# versions.tf — OpenTofu + AWS provider pins for the agent-box auto-stop/wake
# module.
#
# SELF-CONTAINED root module: its own remote state, its own provider, NO
# dependency on the repo-level infra/ root or infra/aws-batch. It only reads the
# agent EC2 instance (a data source) and the Batch queue name (a variable), and
# CREATES the EventBridge schedule, the two Lambdas, the API Gateway HTTP API,
# the DynamoDB streak table, and least-privilege IAM scoped to the one instance.
#
# OpenTofu (MPL-2.0) per PROJECT_STATE decision 2026-06-05 — all HCL is written
# for `tofu`, not BUSL Terraform. aws ~> 5 matches the rest of the AWS infra.

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

  # Remote state in the existing runs bucket so the state is durable and NATE can
  # manage this module from any machine (matches infra/aws-batch). Distinct key
  # so it never collides with the Batch module's state. No DynamoDB lock table —
  # single-operator for now.
  backend "s3" {
    bucket = "grace2-hazard-runs-226996537797"
    key    = "tofu-state/aws-autostop.tfstate"
    region = "us-west-2"
  }
}
