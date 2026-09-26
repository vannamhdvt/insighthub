variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "aws_region" {
  description = "Region, used for the CloudWatch Logs service principal."
  type        = string
}

variable "account_id" {
  description = "AWS account id owning the key."
  type        = string
}
