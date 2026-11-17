# The Terraform and AWS provider versions and the provider's settings, as in
# infra/versions.tf. State stays in this folder, in the same per-project
# workspaces as infra/ (scripts/aws-common.sh sets TF_WORKSPACE for both).

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

  # The same tags as infra/: infra/main.tf finds the volume and the address by
  # their deployment and role tags.
  default_tags {
    tags = {
      app        = "groundline"
      deployment = var.deployment_name
    }
  }
}
