locals {
  # Shared class account (DO2603): every AWS name carries the student prefix.
  name      = "${var.name_prefix}-${var.environment}"
  namespace = "insighthub-${var.environment}"

  # Required tags (policy/terraform/tags.rego) + the DO2603 lab inventory tags.
  tags = {
    project     = "insighthub"
    environment = var.environment
    owner       = var.owner
    cost_center = var.cost_center
    managed_by  = "terraform"
    Class       = "DO2603"
    LabId       = var.lab_id
    Owner       = var.owner
    ExpiresAt   = var.expires_at
  }

  app_service_account = "insighthub"
}

data "aws_caller_identity" "current" {}

module "kms" {
  source = "./modules/kms"

  name       = local.name
  aws_region = var.aws_region
  account_id = data.aws_caller_identity.current.account_id
}

module "network" {
  source = "./modules/network"

  name               = local.name
  cidr               = var.vpc_cidr
  az_count           = var.az_count
  cluster_name       = local.name
  kms_key_arn        = module.kms.key_arn
  log_retention_days = var.log_retention_days
}

module "eks" {
  source = "./modules/eks"

  name                          = local.name
  kubernetes_version            = var.kubernetes_version
  private_subnet_ids            = module.network.private_subnet_ids
  public_access_cidrs           = var.eks_public_access_cidrs
  cluster_admin_principal_arns  = var.cluster_admin_principal_arns
  cluster_viewer_principal_arns = var.cluster_viewer_principal_arns
  kms_key_arn                   = module.kms.key_arn
  log_retention_days            = var.log_retention_days
  node_instance_types           = var.node_instance_types
  node_capacity_type            = var.node_capacity_type
  node_desired_size             = var.node_desired_size
  node_min_size                 = var.node_min_size
  node_max_size                 = var.node_max_size
}

module "rds" {
  source = "./modules/rds"

  name                       = local.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.cluster_security_group_id]
  kms_key_arn                = module.kms.key_arn
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage
  multi_az                   = var.db_multi_az
  backup_retention_days      = var.db_backup_retention_days
  deletion_protection        = var.db_deletion_protection
  log_retention_days         = var.log_retention_days
}

module "elasticache" {
  source = "./modules/elasticache"

  name                       = local.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.cluster_security_group_id]
  kms_key_arn                = module.kms.key_arn
  node_type                  = var.redis_node_type
  num_cache_clusters         = var.redis_num_cache_clusters
}

module "secrets" {
  source = "./modules/secrets"

  name         = local.name
  kms_key_arn  = module.kms.key_arn
  database_url = module.rds.database_url
  redis_url    = module.elasticache.redis_url
}

module "ecr" {
  source = "./modules/ecr"

  name        = local.name
  kms_key_arn = module.kms.key_arn
  services    = ["api", "ingestion-worker", "web"]
}

# IRSA: the application pods read only their runtime secret.
module "irsa_app" {
  source = "./modules/irsa"

  name              = "${local.name}-app"
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_issuer_host  = module.eks.oidc_issuer_host
  namespace         = local.namespace
  service_account   = local.app_service_account
  policy_json       = data.aws_iam_policy_document.app.json
}

data "aws_iam_policy_document" "app" {
  statement {
    sid       = "ReadRuntimeSecret"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [module.secrets.runtime_secret_arn]
  }

  statement {
    sid       = "DecryptRuntimeSecret"
    actions   = ["kms:Decrypt"]
    resources = [module.kms.key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "kubernetes_namespace_v1" "app" {
  metadata {
    name = local.namespace
    labels = {
      "app.kubernetes.io/part-of"          = "insighthub"
      "environment"                        = var.environment
      "pod-security.kubernetes.io/enforce" = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
    }
  }

  depends_on = [module.eks]
}

resource "kubernetes_service_account_v1" "app" {
  metadata {
    name      = local.app_service_account
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = module.irsa_app.role_arn
    }
    labels = {
      "app.kubernetes.io/part-of" = "insighthub"
    }
  }

  # The app never calls the Kubernetes API; the CSI driver requests its own token.
  automount_service_account_token = false
}

# EKS no longer marks a default StorageClass; uploads PVC uses encrypted gp3.
resource "kubernetes_storage_class_v1" "gp3" {
  metadata {
    name = "gp3-encrypted"
    annotations = {
      "storageclass.kubernetes.io/is-default-class" = "true"
    }
  }

  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Delete"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    type      = "gp3"
    encrypted = "true"
    kmsKeyId  = module.kms.key_arn
  }

  depends_on = [module.eks]
}
