variable "name" {
  description = "Cluster name."
  type        = string
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for control plane ENIs and nodes."
  type        = list(string)
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public API endpoint."
  type        = list(string)
}

variable "cluster_admin_principal_arns" {
  description = "Principals given cluster-admin access entries."
  type        = list(string)
}

variable "cluster_viewer_principal_arns" {
  description = "Principals given AmazonEKSAdminViewPolicy access entries."
  type        = list(string)
  default     = []
}

variable "kms_key_arn" {
  description = "CMK for secrets envelope encryption and log group."
  type        = string
}

variable "log_retention_days" {
  description = "Control plane log retention."
  type        = number
}

variable "node_instance_types" {
  description = "Node instance types."
  type        = list(string)
}

variable "node_capacity_type" {
  description = "ON_DEMAND or SPOT."
  type        = string
}

variable "node_desired_size" {
  description = "Desired nodes."
  type        = number
}

variable "node_min_size" {
  description = "Minimum nodes."
  type        = number
}

variable "node_max_size" {
  description = "Maximum nodes."
  type        = number
}
