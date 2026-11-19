# scripts/aws-deploy fills these in from infra/terraform.tfvars.

variable "region" {
  description = "The AWS Region to deploy to: your AWS project's Region."
  type        = string
}

variable "deployment_name" {
  description = "The deployment these belong to, as in infra/variables.tf."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.deployment_name))
    error_message = "deployment_name should be 1 to 20 lowercase letters, digits and hyphens, starting with a letter, such as alice."
  }
}

variable "instance_type" {
  description = "The server's instance type, as in infra/variables.tf. The volume goes in an Availability Zone that offers it, since the server has to run in the volume's zone."
  type        = string
  default     = "t4g.small"

  validation {
    condition     = can(regex("^[a-z]+[0-9]+g[a-z]*\\.", var.instance_type))
    error_message = "instance_type should be a Graviton (arm64) type, such as t4g.small."
  }
}

variable "data_volume_gib" {
  description = "The data volume's size in GiB. It holds the database, the HTTPS certificates and the database passwords, which take well under 1 GiB. A larger size takes effect on the next make aws-up, which grows the volume and then, through infra/server/apply, its filesystem. It can't shrink."
  type        = number
  default     = 5

  validation {
    condition     = var.data_volume_gib >= 1 && var.data_volume_gib <= 100 && floor(var.data_volume_gib) == var.data_volume_gib
    error_message = "data_volume_gib should be a whole number of GiB from 1 to 100."
  }
}

variable "lifetime" {
  description = "disposable or lasting, as in infra/variables.tf. It's kept here too, so a folder still knows it holds a lasting deployment's data after make aws-destroy keeps it."
  type        = string
  default     = "disposable"

  validation {
    condition     = contains(["disposable", "lasting"], var.lifetime)
    error_message = "lifetime must be disposable or lasting."
  }
}

variable "app_settings" {
  description = "The app_settings in infra/terraform.tfvars. Nothing here uses them: they're checked here, so bad settings stop before the data volume and Elastic IP are created."
  type        = map(number)
  default     = {}
}
