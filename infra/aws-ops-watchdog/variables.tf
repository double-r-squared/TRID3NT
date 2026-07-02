# variables.tf -- all tunables for the grace2-ops-watchdog module.
# Override any default by creating a terraform.tfvars in this directory.

variable "region" {
  type        = string
  default     = "us-west-2"
  description = "AWS region to deploy the watchdog into."
}

variable "account_id" {
  type        = string
  default     = "226996537797"
  description = "AWS account ID (used to construct resource ARNs)."
}

variable "alert_email" {
  type        = string
  default     = "natealmanza3@gmail.com"
  description = "Email address for WARN/CRITICAL SNS alerts. Must confirm the subscription."
}

variable "schedule_rate" {
  type        = string
  default     = "rate(15 minutes)"
  description = "EventBridge schedule expression. Decrease to catch problems faster; increase to save cost."
}

# ---- probe thresholds (all exposed so no code edit is needed to tune) ---- #

variable "orphan_crit_min" {
  type        = number
  default     = 6
  description = "Minimum running-task count before the CRITICAL orphan check activates (must exceed this AND crit_delta)."
}

variable "orphan_crit_delta" {
  type        = number
  default     = 3
  description = "running - routes must exceed this value (combined with orphan_crit_min) to trigger CRITICAL."
}

variable "orphan_warn_delta" {
  type        = number
  default     = 2
  description = "running - routes must exceed this value to trigger WARN."
}

variable "vcpu_quota" {
  type        = number
  default     = 64
  description = "Fargate on-demand vCPU quota for the account/region (from AWS Service Quotas)."
}

variable "vcpu_crit_pct" {
  type        = number
  default     = 75
  description = "Percentage of vcpu_quota used that triggers a CRITICAL alert."
}

variable "batch_warn_jobs" {
  type        = number
  default     = 8
  description = "RUNNING Batch solver-job count that triggers a WARN (runaway submit guard)."
}

variable "cf_timeout_s" {
  type        = number
  default     = 8
  description = "HTTP timeout in seconds for the CloudFront edge reachability probe."
}

variable "log_retention_days" {
  type        = number
  default     = 30
  description = "CloudWatch Logs retention period for the watchdog log group."
}
