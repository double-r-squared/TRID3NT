# providers.tf -- AWS provider for the agent-isolation (Fargate-per-session)
# module.
#
# Credentials resolve from the standard AWS credential chain at apply time
# (SSO profile / env vars / ~/.aws/credentials). NATE runs `aws sso login`
# before any `tofu plan/apply` -- the agent never scripts around interactive
# auth (AGENTS.md invariant). This module is a SCAFFOLD; no apply happens here.
#
# default_tags tags every aws_* RESOURCE this module creates (NOT data sources --
# the existing VPC/subnets/cert/ECR repo are read-only here and keep their tags).

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "grace2"
      component  = "agent-isolation"
      managed_by = "opentofu"
    }
  }
}
