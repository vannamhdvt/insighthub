variable "name" {
  description = "IAM role name."
  type        = string
}

variable "oidc_provider_arn" {
  description = "EKS IAM OIDC provider ARN."
  type        = string
}

variable "oidc_issuer_host" {
  description = "OIDC issuer host/path without https://."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace of the service account."
  type        = string
}

variable "service_account" {
  description = "Kubernetes service account name."
  type        = string
}

variable "policy_json" {
  description = "Optional least-privilege inline policy."
  type        = string
  default     = null
}

variable "managed_policy_arns" {
  description = "AWS managed policies to attach."
  type        = list(string)
  default     = []
}
