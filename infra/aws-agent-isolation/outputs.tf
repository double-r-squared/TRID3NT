# outputs.tf -- the handles the migration runbook + the CloudFront cutover need.

output "alb_dns_name" {
  description = "The broker ALB DNS name. The canary hits this on a separate hostname; the CloudFront /ws origin is later repointed here (RUNBOOK step 7)."
  value       = aws_lb.broker.dns_name
}

output "alb_arn" {
  description = "The broker ALB ARN."
  value       = aws_lb.broker.arn
}

output "ecs_cluster_name" {
  description = "The ECS cluster the per-session agent tasks + the broker run in."
  value       = aws_ecs_cluster.agents.name
}

output "agent_task_definition_family" {
  description = "The per-session agent task-definition family the broker RunTasks."
  value       = aws_ecs_task_definition.agent.family
}

output "broker_service_name" {
  description = "The broker ECS service name."
  value       = aws_ecs_service.broker.name
}

output "routes_table_name" {
  description = "The session-route DynamoDB table the broker reads/writes."
  value       = aws_dynamodb_table.session_routes.name
}

output "agent_ecr_repository_url" {
  description = "The agent image ECR repo URL (push target for the grace2-agent-builder CodeBuild project)."
  value       = aws_ecr_repository.agent.repository_url
}

output "agent_builder_project" {
  description = "The off-box CodeBuild project that builds the agent image."
  value       = aws_codebuild_project.agent_builder.name
}

output "task_reaper_function_name" {
  description = "The per-task idle reaper Lambda."
  value       = aws_lambda_function.reaper.function_name
}

output "agent_task_role_arn" {
  description = "The per-session agent task role (mirrors the live EC2 agent role)."
  value       = aws_iam_role.agent_task.arn
}

output "broker_task_role_arn" {
  description = "The broker task role (RunTask/StopTask + routes/users DynamoDB)."
  value       = aws_iam_role.broker_task.arn
}

output "agent_task_security_group_id" {
  description = "The per-session agent task SG (8765/8766 from the broker/reaper only)."
  value       = aws_security_group.agent_task.id
}
