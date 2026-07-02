# outputs.tf -- grace2-ops-watchdog

output "lambda_arn" {
  description = "ARN of the grace2-ops-watchdog Lambda."
  value       = aws_lambda_function.watchdog.arn
}

output "lambda_function_name" {
  description = "Name of the watchdog Lambda (use with aws lambda invoke for manual tests)."
  value       = aws_lambda_function.watchdog.function_name
}

output "sns_topic_arn" {
  description = "ARN of the grace2-ops-alerts SNS topic."
  value       = aws_sns_topic.ops_alerts.arn
}

output "eventbridge_rule_name" {
  description = "EventBridge rule name (disable this rule to pause the watchdog)."
  value       = aws_cloudwatch_event_rule.watchdog_schedule.name
}

output "log_group" {
  description = "CloudWatch Log Group for watchdog run logs."
  value       = aws_cloudwatch_log_group.watchdog.name
}
