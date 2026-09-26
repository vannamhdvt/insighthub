variable "name" {
  description = "Identifier prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the DB subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to connect on 5432."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "CMK for storage and logs."
  type        = string
}

variable "instance_class" {
  description = "Instance class."
  type        = string
}

variable "allocated_storage" {
  description = "Storage GiB."
  type        = number
}

variable "multi_az" {
  description = "Multi-AZ deployment."
  type        = bool
}

variable "backup_retention_days" {
  description = "Automated backup retention days."
  type        = number
}

variable "deletion_protection" {
  description = "Deletion protection."
  type        = bool
}

variable "log_retention_days" {
  description = "Exported log retention."
  type        = number
}
