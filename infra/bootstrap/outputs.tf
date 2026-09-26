output "state_bucket" {
  description = "Use as `bucket` in backend-configs/<env>.s3.tfbackend and GitHub var TF_STATE_BUCKET."
  value       = aws_s3_bucket.state.bucket
}

output "state_kms_key_arn" {
  description = "Use as `kms_key_id` in the backend config and GitHub var TF_STATE_KMS_KEY."
  value       = aws_kms_key.state.arn
}

output "plan_role_arn" {
  description = "GitHub var AWS_PLAN_ROLE_ARN."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "GitHub environment var AWS_APPLY_ROLE_ARN."
  value       = aws_iam_role.apply.arn
}
