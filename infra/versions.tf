# The Terraform and AWS provider versions, and the provider's settings.
# State stays in this folder (terraform.tfstate, git-ignored): there's no
# backend block, since each deployment is disposable and has one owner. Several
# owners can share an AWS project, since resources are named after
# deployment_name.

terraform {
  required_version = "~> 1.17"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.72"
    }
  }
}

provider "aws" {
  region = var.region

  # Tags every resource the provider creates, so a forgotten one is easy to
  # find in the console, along with whose it is. The instance's root disk needs
  # its own (main.tf).
  default_tags {
    tags = {
      project    = "groundline"
      deployment = var.deployment_name
    }
  }
}
