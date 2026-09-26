variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "cidr" {
  description = "VPC CIDR."
  type        = string
}

variable "az_count" {
  description = "Number of AZs."
  type        = number
}

variable "cluster_name" {
  description = "EKS cluster name used in subnet discovery tags."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for the flow log group."
  type        = string
}

variable "log_retention_days" {
  description = "Flow log retention."
  type        = number
}
