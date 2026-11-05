# scripts/aws-deploy fills in region, tester_cidr and git_ref.
# deployment_name and budget_alert_email go in terraform.tfvars (see
# terraform.tfvars.example).

variable "region" {
  description = "The AWS Region to deploy to: your AWS project's Region."
  type        = string
}

variable "deployment_name" {
  description = "Names this deployment's resources, so several can share an AWS project: your name for a test server, or a name such as staging."
  type        = string

  validation {
    # IAM role names can be 64 characters, and groundline-instance- takes 20.
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.deployment_name))
    error_message = "deployment_name should be 1 to 20 lowercase letters, digits and hyphens, starting with a letter, such as alice."
  }
}

variable "auto_terminate_hours" {
  description = "How many hours after its first boot the server terminates itself, deleting its data. 0 keeps it running."
  type        = number
  default     = 5

  validation {
    condition     = var.auto_terminate_hours >= 0 && floor(var.auto_terminate_hours) == var.auto_terminate_hours
    error_message = "auto_terminate_hours should be a whole number of hours, or 0 to keep the server running."
  }
}

variable "budget_alert_email" {
  description = "The email address the monthly budget alert goes to."
  type        = string
}

variable "tester_cidr" {
  description = "The one IPv4 address allowed to reach the app, as a /32, such as 203.0.113.7/32."
  type        = string

  validation {
    condition = (
      can(regex("^(\\d{1,3}\\.){3}\\d{1,3}/32$", var.tester_cidr))
      && can(cidrhost(var.tester_cidr, 0))
    )
    error_message = "tester_cidr should be a single IPv4 address with /32, such as 203.0.113.7/32."
  }
}

variable "git_ref" {
  description = "The commit of repository_url the server checks out and runs."
  type        = string
}

variable "instance_type" {
  description = "The EC2 instance type. It must be a Graviton (arm64) type, since the AMI and the startup script are arm64."
  type        = string
  default     = "t4g.small"

  validation {
    # Graviton types have a g after the generation number: t4g, m7g, c7gn.
    condition     = can(regex("^[a-z]+[0-9]+g[a-z]*\\.", var.instance_type))
    error_message = "instance_type should be a Graviton (arm64) type, such as t4g.small or t4g.medium."
  }
}

variable "repository_url" {
  description = "The git repository the server clones."
  type        = string
  default     = "https://github.com/groundline-dev/groundline.git"
}

variable "anthropic_parameter_name" {
  description = "The SecureString parameter in Parameter Store that holds the Anthropic API key. The scripts expect the default."
  type        = string
  default     = "/groundline/anthropic-api-key"

  validation {
    condition     = startswith(var.anthropic_parameter_name, "/")
    error_message = "anthropic_parameter_name should start with /, such as /groundline/anthropic-api-key."
  }
}
