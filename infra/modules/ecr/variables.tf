variable "name" {
  description = "Repository prefix."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for image encryption."
  type        = string
}

variable "services" {
  description = "One repository per service."
  type        = list(string)
}
