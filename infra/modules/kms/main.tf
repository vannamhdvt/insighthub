# One customer-managed key per environment for EKS secrets, EBS, RDS,
# ElastiCache, Secrets Manager, ECR and CloudWatch Logs.
resource "aws_kms_key" "this" {
  description             = "${var.name} data encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.key.json
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.this.key_id
}

data "aws_iam_policy_document" "key" {
  #checkov:skip=CKV_AWS_111:Key policy: Resource "*" means this key only; usage is scoped by IAM policies.
  #checkov:skip=CKV_AWS_356:Key policy: Resource "*" means this key only.
  #checkov:skip=CKV_AWS_109:Key policy: account-root administration is the AWS-recommended default so IAM can delegate.
  # Account-level administration; IAM policies then scope who can use the key.
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.account_id}:root"]
    }
  }

  statement {
    sid = "CloudWatchLogs"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:*"]
    }
  }
}
