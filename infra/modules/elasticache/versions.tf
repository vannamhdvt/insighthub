terraform {
  required_version = ">= 1.10.0"
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}
