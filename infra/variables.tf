variable "aws_region" {
  description = "AWS region for every lab resource."
  type        = string
  default     = "ap-southeast-1"
}

variable "name_prefix" {
  description = "Per-student prefix for every AWS resource name in the shared DO2603 account."
  type        = string
  default     = "do2603-namtv"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name_prefix))
    error_message = "name_prefix: 3-21 lowercase letters, digits or hyphens."
  }
}

variable "environment" {
  description = "Environment name; the Kubernetes namespace is insighthub-<environment>."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "owner" {
  description = "Person accountable for the lab resources and their teardown (tag owner)."
  type        = string
}

variable "cost_center" {
  description = "Cost center tag value."
  type        = string
  default     = "do2603"
}

variable "lab_id" {
  description = "Unique id of this lab run (tag LabId), e.g. do2603-namtv-day3-01."
  type        = string
}

variable "expires_at" {
  description = "RFC3339 time by which this lab run must be destroyed (tag ExpiresAt)."
  type        = string

  validation {
    condition     = can(formatdate("YYYY", var.expires_at))
    error_message = "expires_at must be an RFC3339 timestamp, e.g. 2026-09-23T17:00:00Z."
  }
}

variable "vpc_cidr" {
  description = "CIDR of the lab VPC."
  type        = string
  default     = "10.60.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones (EKS and RDS subnet groups need at least 2)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version (must be in EKS standard support)."
  type        = string
  default     = "1.34"
}

variable "eks_public_access_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint. Access still requires an IAM access entry."
  type        = list(string)
}

variable "cluster_admin_principal_arns" {
  description = "IAM role/user ARNs granted cluster-admin through EKS access entries (e.g. the lab SSO role, the CI role)."
  type        = list(string)
  default     = []
}

variable "cluster_viewer_principal_arns" {
  description = "IAM role ARNs granted read-only (incl. secrets) access, e.g. the CI plan role that refreshes helm releases."
  type        = list(string)
  default     = []
}

variable "node_instance_types" {
  description = "Instance types for the managed node group."
  type        = list(string)
  default     = ["t3.medium"]
}

variable "node_capacity_type" {
  description = "ON_DEMAND or SPOT."
  type        = string
  default     = "ON_DEMAND"
}

variable "node_desired_size" {
  description = "Desired node count."
  type        = number
  default     = 2
}

variable "node_min_size" {
  description = "Minimum node count."
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum node count."
  type        = number
  default     = 3
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GiB."
  type        = number
  default     = 20
}

variable "db_multi_az" {
  description = "Multi-AZ RDS. Required true for prod by policy."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "RDS automated backup retention. Policy requires >= 7 for prod."
  type        = number
  default     = 1
}

variable "db_deletion_protection" {
  description = "RDS deletion protection. Policy requires true for prod."
  type        = bool
  default     = false
}

variable "redis_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.micro"
}

variable "redis_num_cache_clusters" {
  description = "Nodes in the Redis replication group (>= 2 enables automatic failover)."
  type        = number
  default     = 1
}

variable "log_retention_days" {
  description = "CloudWatch retention for control plane, VPC flow and RDS logs."
  type        = number
  default     = 7
}

variable "install_cluster_addons" {
  description = "Install ingress-nginx, cert-manager and the Secrets Store CSI driver with Helm."
  type        = bool
  default     = true
}
