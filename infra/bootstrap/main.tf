# One-time bootstrap per AWS account (run locally with the lab profile):
#   - S3 bucket for Terraform state (native lockfile, versioned, KMS, private)
#   - GitHub Actions OIDC provider + two roles (plan: read-only, apply: env-gated)
# State of this root stays local on purpose (chicken-and-egg); keep the file safe
# or re-import. See docs/runbooks/day3-aws-lab.md.
terraform {
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project     = "insighthub"
      environment = "shared"
      owner       = var.owner
      cost_center = var.cost_center
      managed_by  = "terraform"
      Class       = "DO2603"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  bucket     = "${var.name_prefix}-tfstate-${local.account_id}"
  role_arn   = "arn:aws:iam::${local.account_id}:role/${var.name_prefix}-*"
  repo_sub   = "repo:${var.github_repository}"
}

# ------------------------------------------------------------------ state bucket
resource "aws_kms_key" "state" {
  description             = "InsightHub Terraform state"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.state_key.json
}

data "aws_iam_policy_document" "state_key" {
  #checkov:skip=CKV_AWS_111:Key policy: Resource "*" means this key only; usage is scoped by IAM policies.
  #checkov:skip=CKV_AWS_356:Key policy: Resource "*" means this key only.
  #checkov:skip=CKV_AWS_109:Key policy: account-root administration is the AWS-recommended default so IAM can delegate.
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_kms_alias" "state" {
  name          = "alias/${var.name_prefix}-tfstate"
  target_key_id = aws_kms_key.state.key_id
}

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_18:Access logging needs a second log bucket; CloudTrail data events cover the lab audit need.
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for a disposable lab state bucket.
  #checkov:skip=CKV2_AWS_62:No consumer for state change notifications in the lab.
  bucket = local.bucket
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_bucket.json
}

# ---------------------------------------------------------- GitHub Actions OIDC
# The GitHub OIDC provider is one per account. In the shared DO2603 account it
# may already exist (another student or the trainer): pass its ARN instead of
# creating a duplicate (CreateOpenIDConnectProvider would fail with EntityAlreadyExists).
resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_oidc_provider_arn == null ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  github_oidc_provider_arn = coalesce(var.github_oidc_provider_arn, try(aws_iam_openid_connect_provider.github[0].arn, null))
}

# PR and main-branch runs: plan only.
data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.repo_sub}:pull_request", "${local.repo_sub}:ref:refs/heads/main"]
    }
  }
}

# Apply/deploy: only jobs bound to a protected GitHub environment (manual approval).
data "aws_iam_policy_document" "apply_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for env in var.github_environments : "${local.repo_sub}:environment:${env}"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "${var.name_prefix}-gha-plan"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role" "apply" {
  name                 = "${var.name_prefix}-gha-apply"
  assume_role_policy   = data.aws_iam_policy_document.apply_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# The apply role must not exceed what the class grants students (do_2603_policy
# + ELB): no PowerUser/Admin. Same service families, nothing account-wide beyond.
data "aws_iam_policy_document" "apply_services" {
  #checkov:skip=CKV_AWS_111:Terraform creates these resources, so ARNs are unknown before apply; scope equals the class student policy and is gated by the protected environment.
  #checkov:skip=CKV_AWS_356:Same as CKV_AWS_111: resource ARNs do not exist before the first apply.
  #checkov:skip=CKV_AWS_109:kms:PutKeyPolicy/secretsmanager policy actions are needed to create the CMK and secret; IAM itself is limited to name_prefix below.
  #checkov:skip=CKV_AWS_108:Secret reads are needed to refresh the runtime secret version; the prefix scoping is enforced by naming and review.
  #checkov:skip=CKV_AWS_107:ecr:GetAuthorizationToken is required for the build job to push images; the role is only assumable from the protected environment.
  #checkov:skip=CKV_AWS_290:Write access mirrors the class-granted services only (no iam:* here).
  #checkov:skip=CKV_AWS_355:Resource "*" for create-style actions whose ARNs are unknown before apply.
  statement {
    sid = "LabServices"
    actions = [
      "ec2:*", "eks:*", "rds:*", "ecr:*", "logs:*", "cloudwatch:*",
      "elasticloadbalancing:*", "elasticache:*", "secretsmanager:*", "kms:*",
      "autoscaling:Describe*", "iam:Get*", "iam:List*", "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "apply_services" {
  name   = "lab-services"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_services.json
}

data "aws_iam_policy_document" "state_access" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    sid       = "ReadWriteStateAndLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/insighthub/*"]
  }
  statement {
    sid       = "StateKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.state.arn]
  }
}

# Plan refreshes the runtime secret version and must be able to decrypt it.
data "aws_iam_policy_document" "plan_extra" {
  source_policy_documents = [data.aws_iam_policy_document.state_access.json]

  statement {
    sid       = "RefreshRuntimeSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:${var.name_prefix}-*"]
  }
  statement {
    sid       = "DecryptViaSecretsManager"
    actions   = ["kms:Decrypt"]
    resources = ["arn:aws:kms:${var.aws_region}:${local.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "plan_extra" {
  name   = "state-and-secret-refresh"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan_extra.json
}

data "aws_iam_policy_document" "apply_iam" {
  source_policy_documents = [data.aws_iam_policy_document.state_access.json]

  statement {
    sid = "ManageInsightHubRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListInstanceProfilesForRole",
      "iam:PassRole",
    ]
    resources = [local.role_arn]
  }
  statement {
    sid = "ManageClusterOidcProvider"
    actions = [
      "iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider", "iam:TagOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
    ]
    resources = ["arn:aws:iam::${local.account_id}:oidc-provider/oidc.eks.${var.aws_region}.amazonaws.com/*"]
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/aws-service-role/*"]
  }
}

resource "aws_iam_role_policy" "apply_iam" {
  name   = "state-and-scoped-iam"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply_iam.json
}
