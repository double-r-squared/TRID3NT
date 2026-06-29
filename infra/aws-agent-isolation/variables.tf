# variables.tf -- inputs for the agent-isolation (Fargate-per-session) module.
#
# Defaults match the hand-provisioned prod environment (account 226996537797,
# us-west-2) WHERE A SAFE DEFAULT EXISTS. The variables with NO default are the
# live-value TODOs that MUST be filled before a plan/apply (subnets, security
# groups, the ACM cert, the agent ECR image). See terraform.tfvars.example and
# the TODO list at the top of RUNBOOK.md.

# --------------------------------------------------------------------------- #
# Account / region
# --------------------------------------------------------------------------- #

variable "region" {
  type        = string
  description = "AWS region for all resources."
  default     = "us-west-2"
}

variable "account_id" {
  type        = string
  description = "AWS account id (used to build ARNs for least-privilege IAM)."
  default     = "226996537797"
}

# --------------------------------------------------------------------------- #
# Networking -- LIVE VALUES REQUIRED (no defaults). The Fargate tasks + the ALB
# + the broker run in the EXISTING VPC. Fill from the live account.
# --------------------------------------------------------------------------- #

variable "vpc_id" {
  type        = string
  description = "TODO(live): the VPC id the agent EC2 box / TiTiler box already run in. The ALB, the Fargate tasks, and the broker all live here."
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "TODO(live): >=2 public subnet ids (across AZs) for the internet-facing ALB."
}

variable "task_subnet_ids" {
  type        = list(string)
  description = "TODO(live): subnet ids the Fargate tasks + broker run in. Private subnets with a NAT egress are preferred (Bedrock/Cognito/S3/DynamoDB reachability); public subnets with assign_public_ip work too. The broker reaches each task on its private IP, so the broker + tasks MUST share routable subnets."
}

variable "acm_certificate_arn" {
  type        = string
  description = "TODO(live): ACM cert ARN for the ALB HTTPS/WSS listener. Covers the broker hostname the canary uses (and, post-cutover, whatever CloudFront /ws origin-points at). us-west-2 regional cert (ALB is regional, NOT the CloudFront us-east-1 cert)."
}

variable "agent_image" {
  type        = string
  description = "TODO(live): the agent ECR image ref the Fargate task runs, e.g. 226996537797.dkr.ecr.us-west-2.amazonaws.com/grace2-agent:latest. Built off-box via the grace2-agent-builder CodeBuild project (codebuild.tf + buildspec.agent.yml). Pin to a digest for a real cutover."
}

# --------------------------------------------------------------------------- #
# Cognito -- the broker verifies ID tokens against this pool (reuses the live
# pool, same values the agent + the wake/case Lambdas use). Defaults match
# infra/aws-autostop/terraform.tfvars (public SPA ids, NOT secrets).
# --------------------------------------------------------------------------- #

variable "cognito_user_pool_id" {
  type        = string
  description = "Cognito user pool id the broker verifies ID tokens against (GRACE2_COGNITO_USER_POOL_ID). Same pool the agent + wake Lambda use."
  default     = "us-west-2_mIpKrr727"
}

variable "cognito_client_id" {
  type        = string
  description = "Cognito SPA app client id (the ID token aud). Same value baked into the web bundle."
  default     = "43ovkrtt97oh6gsnl006aecera"
}

# --------------------------------------------------------------------------- #
# DynamoDB
# --------------------------------------------------------------------------- #

variable "routes_table_name" {
  type        = string
  description = "Name of the session-route table the broker reads/writes (PK user_ulid, SK session_id)."
  default     = "grace2_session_routes"
}

variable "users_table_name" {
  type        = string
  description = "Existing users table the broker resolves Cognito sub -> internal ULID against (firebase_uid-index GSI) AND first-connect-provisions a row into. Mirrors USERS_TABLE in the case Lambdas + Persistence.get_user_by_firebase_uid. trid3nt_users = the live table post the 2026-06-29 DynamoDB rename (the agent task def already uses the trid3nt_ prefix, so they must match)."
  default     = "trid3nt_users"
}

variable "users_firebase_uid_index" {
  type        = string
  description = "GSI on the users table mapping firebase_uid (Cognito sub) -> the user doc whose _id is the internal ULID. Mirrors _TABLE_GSIS['users'] in dynamo_backend.py."
  default     = "firebase_uid-index"
}

variable "cache_bucket" {
  type        = string
  description = "S3 cache bucket (GRACE2_CACHE_BUCKET) the agent task uses. Same as the live box."
  default     = "grace2-hazard-cache-226996537797"
}

variable "runs_bucket" {
  type        = string
  description = "S3 runs bucket (GRACE2_RUNS_BUCKET) the agent task uses. Same as the live box; also holds the tofu remote state."
  default     = "grace2-hazard-runs-226996537797"
}

variable "route_ttl_seconds" {
  type        = number
  description = <<-EOT
    TTL (seconds) on a session-route row. A safety reaper only: the per-task
    idle reaper deletes the route on StopTask; the TTL just garbage-collects any
    orphaned row (e.g. a task that vanished without a clean stop) so the table
    self-heals. Generous so a long-but-idle session is never evicted out from
    under a live task. 86400 = 24h.
  EOT
  default     = 86400
}

# --------------------------------------------------------------------------- #
# ECS / Fargate task sizing -- the per-session agent task. The agent loop is
# I/O-bound (Bedrock + Batch-poll + S3), so modest CPU/RAM; the geo-heavy IMAGE
# (not the runtime CPU) is the cold-start cost. Sized within the Fargate
# CPU/memory matrix (1 vCPU requires 2-8 GB).
# --------------------------------------------------------------------------- #

variable "agent_task_cpu" {
  type        = string
  description = "Fargate task CPU units for the per-session agent task. 1024 = 1 vCPU. Valid pairings with agent_task_memory per the Fargate matrix."
  default     = "1024"
}

variable "agent_task_memory" {
  type        = string
  description = "Fargate task memory (MiB) for the per-session agent task. 2048 = 2 GB. Headroom for the geo/tool-definition closure + the in-loop peak."
  default     = "2048"
}

variable "agent_log_retention_days" {
  type        = number
  description = "CloudWatch Logs retention for the per-session agent task logs."
  default     = 14
}

# --------------------------------------------------------------------------- #
# ALB -- the long idle timeout is the WHOLE POINT (hours-long WS turns). The
# spike calls out AVOIDING API Gateway WebSocket (its 2h connection / 10-min
# idle caps would sever a long SFINCS turn). ALB idle timeout maxes at 4000s,
# which the 12s server-push DATA heartbeat keeps well under (the connection is
# never idle past 12s, so it is never reaped on idle -- the 4000s ceiling is the
# belt to the heartbeat's suspenders).
# --------------------------------------------------------------------------- #

variable "alb_idle_timeout_seconds" {
  type        = number
  description = "ALB idle timeout (seconds). MAX 4000. The agent's 12s server-push heartbeat keeps the WS never-idle, so this is a safety ceiling, not the keepalive. Set high so a heartbeat hiccup never severs a long turn."
  default     = 4000
}

# --------------------------------------------------------------------------- #
# Broker service sizing (the thin always-on connection broker).
# --------------------------------------------------------------------------- #

variable "broker_image" {
  type        = string
  description = "The broker ECR image ref (built from infra/aws-agent-isolation/broker/ by the grace2-broker-builder CodeBuild project, codebuild.tf + buildspec.broker.yml). Defaults to the :latest tag in the grace2-broker repo so the broker task def resolves without a tfvars entry; pin to a digest for a real cutover. A separate image from the agent image."
  default     = "226996537797.dkr.ecr.us-west-2.amazonaws.com/grace2-broker:latest"
}

variable "broker_cpu" {
  type        = string
  description = "Fargate task CPU units for the broker (thin proxy -- small)."
  default     = "512"
}

variable "broker_memory" {
  type        = string
  description = "Fargate task memory (MiB) for the broker."
  default     = "1024"
}

variable "broker_desired_count" {
  type        = number
  description = "Number of broker tasks. 1 is fine for current load; >=2 for HA once cut over. The broker is stateless (all state in DynamoDB), so it scales horizontally."
  default     = 1
}

# --------------------------------------------------------------------------- #
# Per-task idle reaper -- generalizes infra/aws-autostop/idle_check from
# ec2:StopInstances to ecs:StopTask, reusing the SAME busy/streak/G3-Batch logic.
# --------------------------------------------------------------------------- #

variable "idle_threshold_checks" {
  type        = number
  description = "Consecutive idle polls before a per-session task is StopTask'd. With a 5-minute schedule, 3 ~= 15 min confirmed idle (matches the single-box autostop). Any busy signal (in-flight turn/solve, in-flight Batch job, unreadable health) resets the streak."
  default     = 3
}

variable "reaper_schedule_expression" {
  type        = string
  description = "EventBridge schedule for the per-task idle reaper Lambda."
  default     = "rate(5 minutes)"
}

variable "batch_queues" {
  type        = string
  description = "Comma-separated Batch job-queue names the reaper checks (G3 guard): any SUBMITTED..RUNNING job keeps the OWNING session's task up so it can poll the solve to completion. Matches infra/aws-batch."
  default     = "grace2-solvers"
}

variable "reaper_health_timeout_s" {
  type        = number
  description = "HTTP timeout (seconds) for the reaper's per-task /api/health probe. A timeout counts as busy (fail-safe)."
  default     = 5
}

variable "reaper_dry_run" {
  type        = bool
  description = "When true the reaper LOGS the StopTask decision but does NOT call StopTask -- lets the orchestrator validate against live canary tasks before arming. Flip to false to enable per-task auto-stop."
  default     = true
}
