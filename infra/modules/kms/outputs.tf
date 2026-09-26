output "key_arn" {
  description = "KMS key ARN."
  value       = aws_kms_key.this.arn
}
