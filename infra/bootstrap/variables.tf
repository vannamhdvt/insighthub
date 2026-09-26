variable "name_prefix" {
  description = "Per-student prefix (shared DO2603 account): bucket, KMS alias, CI roles, IAM scope."
  type        = string
  default     = "do2603-namtv"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name_prefix))
    error_message = "name_prefix: 3-21 lowercase letters, digits or hyphens."
  }
}

variable "github_oidc_provider_arn" {
  description = "Existing token.actions.githubusercontent.com provider ARN in the account; null creates it."
  type        = string
  default     = null
}

variable "aws_region" {
  description = "Region of the state bucket and lab resources."
  type        = string
  default     = "ap-southeast-1"
}

variable "github_repository" {
  description = "owner/repo allowed to assume the CI roles."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use owner/repo, no wildcard."
  }
}

variable "github_environments" {
  description = "Protected GitHub environments allowed to apply/deploy."
  type        = list(string)
  default     = ["dev"]
}

variable "owner" {
  description = "Owner tag."
  type        = string
}

variable "cost_center" {
  description = "Cost center tag."
  type        = string
  default     = "do2603"
}
