# Remote state: S3 with native lockfile (Terraform >= 1.10). No DynamoDB lock table.
# Bucket/key/region are per environment and passed at init time:
#   terraform init -backend-config=backend-configs/dev.s3.tfbackend
# The bucket is created once by infra/bootstrap (versioned, KMS-encrypted, private).
terraform {
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
