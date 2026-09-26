output "role_arn" {
  description = "IRSA role ARN (annotate the service account with it)."
  value       = aws_iam_role.this.arn
}
