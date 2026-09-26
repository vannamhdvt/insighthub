output "runtime_secret_arn" {
  description = "Secret ARN."
  value       = aws_secretsmanager_secret.runtime.arn
}

output "runtime_secret_name" {
  description = "Secret name."
  value       = aws_secretsmanager_secret.runtime.name
}
