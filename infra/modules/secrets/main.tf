# Runtime connection strings for api/worker. Pods read it with their IRSA role
# through the Secrets Store CSI driver; nothing is stored in Git or Helm values.
resource "aws_secretsmanager_secret" "runtime" {
  #checkov:skip=CKV2_AWS_57:Credentials are regenerated every lab run by Terraform (random_password); managed rotation needs a Lambda outside lab scope.
  name                    = "${var.name}/runtime"
  description             = "InsightHub DATABASE_URL and REDIS_URL"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "runtime" {
  secret_id = aws_secretsmanager_secret.runtime.id
  secret_string = jsonencode({
    database_url = var.database_url
    redis_url    = var.redis_url
  })
}
