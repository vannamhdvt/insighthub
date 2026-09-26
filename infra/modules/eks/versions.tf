terraform {
  required_version = ">= 1.10.0"
  required_providers {
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.4"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}
