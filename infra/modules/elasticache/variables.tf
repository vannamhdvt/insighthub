variable "name" {
  description = "Replication group id."
  type        = string
}

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed on 6379."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "CMK for at-rest encryption."
  type        = string
}

variable "node_type" {
  description = "Node type."
  type        = string
}

variable "num_cache_clusters" {
  description = "Number of nodes; >1 enables failover."
  type        = number
}
