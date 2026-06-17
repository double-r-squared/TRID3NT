# outputs.tf — values NATE reads after `tofu apply` to configure the env-flip
# on the agent EC2 box (RUNBOOK.md § Step 3).

output "job_queue_name" {
  description = "Name of the Batch job queue. Set GRACE2_AWS_BATCH_QUEUE to this value on the agent."
  value       = aws_batch_job_queue.solvers.name
}

output "job_definition_name" {
  description = "Name of the SFINCS Batch job definition (without revision suffix). Set GRACE2_AWS_BATCH_JOB_DEF to this value on the agent."
  value       = aws_batch_job_definition.sfincs.name
}

output "ecr_repository_url" {
  description = "Full ECR repository URL (account.dkr.ecr.region.amazonaws.com/name). Use this as the image tag base when building and pushing the worker image."
  value       = aws_ecr_repository.sfincs.repository_url
}

output "compute_environment_arn" {
  description = "ARN of the Batch compute environment. Reference this when adding additional job queues or compute environments in future."
  value       = aws_batch_compute_environment.sfincs_spot.arn
}

output "batch_service_role_arn" {
  description = "ARN of the Batch service role. Useful for auditing IAM permissions."
  value       = aws_iam_role.batch_service.arn
}

output "job_task_role_arn" {
  description = "ARN of the ECS task role used by Batch job containers. Needed if adding additional job definitions that share this role."
  value       = aws_iam_role.job_task.arn
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch log group where all Batch job stdout/stderr streams."
  value       = aws_cloudwatch_log_group.batch.name
}
