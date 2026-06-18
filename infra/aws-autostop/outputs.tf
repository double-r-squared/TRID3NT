# outputs.tf — values NATE reads after `tofu apply`.

output "wake_endpoint_url" {
  description = <<-EOT
    Full wake endpoint URL. Set the web build's VITE_GRACE2_WAKE_URL to this so
    the client can wake the box when the WebSocket is down. POST or GET /wake
    starts the instance if it is stopped (no-op if already running).
  EOT
  value       = "${aws_apigatewayv2_stage.default.invoke_url}/wake"
}

output "idle_check_function_name" {
  description = "Name of the idle-check Lambda (for manual `aws lambda invoke` smoke tests)."
  value       = aws_lambda_function.idle_check.function_name
}

output "wake_function_name" {
  description = "Name of the wake Lambda."
  value       = aws_lambda_function.wake.function_name
}

output "state_table_name" {
  description = "DynamoDB table holding the consecutive-idle streak (one item per instance)."
  value       = aws_dynamodb_table.state.name
}

output "schedule_rule_name" {
  description = "EventBridge rule that fires the idle-check Lambda on schedule."
  value       = aws_cloudwatch_event_rule.idle_check.name
}

output "agent_instance_id" {
  description = "The instance the stop/start actions are scoped to."
  value       = data.aws_instance.agent.id
}

output "dry_run" {
  description = "True when auto-stop is in DRY_RUN (logs the decision, does not stop). Flip var.dry_run to arm."
  value       = var.dry_run
}
