# variables.tf — inputs for the agent-box auto-stop/wake module.
#
# Defaults match the hand-provisioned prod environment (account 226996537797,
# us-west-2). Override any in a tfvars file without editing this file.

variable "region" {
  type        = string
  description = "AWS region for all resources."
  default     = "us-west-2"
}

variable "account_id" {
  type        = string
  description = "AWS account id (used to build the instance ARN for least-privilege IAM)."
  default     = "226996537797"
}

variable "agent_instance_id" {
  type        = string
  description = "EC2 instance id of the always-on agent box (grace2-agent). Stop/Start are scoped to THIS instance ARN only."
  default     = "i-0251879a278df797f"
}

variable "health_url" {
  type        = string
  description = <<-EOT
    Full URL the idle-check Lambda polls for the agent's liveness signal. Points
    at the agent's /api/health (catalog HTTP listener, port 8766) via the public
    edge. Prefer the CloudFront distribution (E2L74AS56MVZ87) once /api/* is
    routed to the agent's HTTP origin; the EIP form
    http://54.185.114.233:8766/api/health works too but is plaintext and depends
    on the security group allowing the Lambda's egress IP (Lambda runs outside a
    VPC by default, so it reaches the EIP over the public internet). Set this to
    whatever URL actually serves the agent health JSON in your environment.
  EOT
  default     = "http://54.185.114.233:8766/api/health"
}

variable "idle_threshold_checks" {
  type        = number
  description = <<-EOT
    Number of CONSECUTIVE idle polls required before the box is stopped. With
    schedule_expression at 5 minutes, 3 checks ~= 15 minutes of confirmed idle.
    The auto-stop is bulletproof: any busy signal (live connection, busy flag,
    in-flight Batch solve, unreachable health) resets the streak to zero.
  EOT
  default     = 3
}

variable "schedule_expression" {
  type        = string
  description = "EventBridge schedule for the idle-check Lambda. Default: every 5 minutes."
  default     = "rate(5 minutes)"
}

variable "batch_queues" {
  type        = string
  description = <<-EOT
    Comma-separated AWS Batch job-queue names the idle-check Lambda inspects for
    in-flight solves. Any non-terminal job (SUBMITTED..RUNNING) keeps the box up.
    Matches the queue created by infra/aws-batch (aws_batch_job_queue.solvers).
    Set to "" to disable the Batch guard (only safe if Batch is unused).
  EOT
  default     = "grace2-solvers"
}

variable "health_timeout_s" {
  type        = number
  description = "HTTP timeout (seconds) for the health probe. A timeout counts as busy (fail-safe)."
  default     = 5
}

variable "dry_run" {
  type        = bool
  description = <<-EOT
    When true the idle-check Lambda LOGS the stop decision but does NOT call
    StopInstances. Lets the orchestrator validate behaviour against the live box
    before arming the real stop. Flip to false to enable auto-stop.
  EOT
  default     = false
}

variable "lambda_log_retention_days" {
  type        = number
  description = "CloudWatch Logs retention for both Lambdas."
  default     = 14
}

# ─────────────────────────────────────────────────────────────────────────────
# View-signer Lambda (GET /case-view-url) — "view a Case with the agent OFF".
# Issues a pre-signed S3 GET URL for the materialized Case-view snapshot the
# agent writes to s3://<runs_bucket>/case-views/{case_id}.json. Tiered by
# Cognito sign-in: a verified owner gets signed_ttl (12h), anyone else anon_ttl
# (15min). The bucket stays private; the only read path is a URL this Lambda
# signs. Routes through the EXISTING wake API Gateway HTTP API.
# ─────────────────────────────────────────────────────────────────────────────

variable "runs_bucket" {
  type        = string
  description = <<-EOT
    Durable runs bucket holding the Case-view snapshots under the case-views/
    prefix (GRACE2_RUNS_BUCKET). The signer's IAM role is granted s3:GetObject
    ONLY on arn:aws:s3:::<runs_bucket>/case-views/* — no list, no put, no other
    prefix. The agent (a separate role) writes the snapshots.
  EOT
  default     = "grace2-hazard-runs-226996537797"
}

variable "cognito_user_pool_id" {
  type        = string
  description = <<-EOT
    Cognito user pool id (e.g. us-west-2_AbCdEf123) injected into the signer as
    GRACE2_COGNITO_USER_POOL_ID. UNSET (the demo default "") ⇒ the verifier
    returns None for every token and every request gets the anonymous (short)
    TTL — the live demo is unaffected. Mirrors the agent's gate.
  EOT
  default     = ""
}

variable "cognito_client_id" {
  type        = string
  description = <<-EOT
    Cognito SPA app client id injected as GRACE2_COGNITO_CLIENT_ID. ID tokens
    carry this in their `aud` claim; the verifier fails closed when it is unset
    while a pool IS configured (misconfiguration ⇒ reject rather than accept any
    audience).
  EOT
  default     = ""
}

variable "view_signed_ttl_s" {
  type        = number
  description = <<-EOT
    Pre-signed URL expiry (seconds) for a VERIFIED signed-in owner. Default
    43200 = 12h; the client re-issues on demand so it is effectively unlimited.
    Injected as SIGNED_TTL.
  EOT
  default     = 43200
}

variable "view_anon_ttl_s" {
  type        = number
  description = <<-EOT
    Pre-signed URL expiry (seconds) for the anonymous / public-demo path (no
    token, invalid token, or no pool configured). Default 900 = 15min. Injected
    as ANON_TTL.
  EOT
  default     = 900
}

variable "cases_table" {
  type        = string
  description = <<-EOT
    DynamoDB cases table (GRACE2_DYNAMO_TABLE_PREFIX + "cases" on the agent
    side; the live default is grace2_cases). The case-list Lambda Queries its
    user_id-index / owner_user_id-index GSIs (owner-scoped listing), and the
    view-signer GetItems it for the cold-open Case-shell fallback. The case-list
    role is granted dynamodb:Query + dynamodb:GetItem on this table ARN AND its
    /index/* ONLY; the view-signer role gains dynamodb:GetItem on the table ARN.
  EOT
  default     = "grace2_cases"
}
