# providers.tf — AWS provider configuration for the SFINCS Batch module.
#
# Credentials: resolved from the standard AWS credential chain at apply time
# (IAM role, environment variables, ~/.aws/credentials, or SSO profile). NATE
# runs `aws configure sso` / `aws sso login` before `tofu apply` — the agent
# never scripted around interactive auth (AGENTS.md invariant).
#
# Default tags applied to every resource this module creates. Resources that
# already exist (data sources) are NOT tagged — this module only reads them.
#   project:    grace2            — cost-center alignment in AWS Cost Explorer
#   component:  sfincs-batch      — resource grouping for this compute surface
#   managed_by: opentofu          — drift detection signal for future audits
#
# IMPORTANT: the `default_tags` block tags every aws_* RESOURCE in this module.
# It does NOT affect data sources (they read existing resources). The agent
# EC2 instance role (data.aws_iam_role.agent) and the VPC / subnets / S3 bucket
# are all data sources — they carry their original hand-applied tags unchanged.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "grace2"
      component  = "sfincs-batch"
      managed_by = "opentofu"
    }
  }
}
