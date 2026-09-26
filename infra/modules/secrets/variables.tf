variable "name" {
  description = "Secret name prefix."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for the secret."
  type        = string
}

variable "database_url" {
  description = "PostgreSQL URL."
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Redis URL."
  type        = string
  sensitive   = true
}
