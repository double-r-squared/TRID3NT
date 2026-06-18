# providers.tf — AWS provider for the agent-box auto-stop/wake module.
#
# Credentials resolve from the standard AWS credential chain at apply time
# (SSO profile / env vars / ~/.aws/credentials). NATE runs `aws sso login`
# before `tofu apply` — the agent never scripts around interactive auth
# (AGENTS.md invariant).
#
# default_tags tags every aws_* RESOURCE this module creates (NOT data sources —
# the agent EC2 instance is read-only here and keeps its original tags).

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "grace2"
      component  = "agent-autostop"
      managed_by = "opentofu"
    }
  }
}
