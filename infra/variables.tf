# scripts/aws-deploy fills in region, tester_cidr and git_ref.
# deployment_name and budget_alert_email go in terraform.tfvars (see
# terraform.tfvars.example).

variable "region" {
  description = "The AWS Region to deploy to: your AWS project's Region."
  type        = string
}

variable "deployment_name" {
  description = "Names this deployment's resources, so several can share an AWS project: your name for a test deployment, or a name such as staging."
  type        = string

  validation {
    # IAM role names can be 64 characters, and groundline-instance- takes 20.
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.deployment_name))
    error_message = "deployment_name should be 1 to 20 lowercase letters, digits and hyphens, starting with a letter, such as alice."
  }
}

variable "auto_terminate_hours" {
  description = "How many hours after its first boot the server terminates itself, deleting its data, and the most make aws-reset-deadline can give it from the time it runs. 0 keeps it running."
  type        = number
  default     = 8

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
  default     = null

  validation {
    condition = (
      var.tester_cidr == null ? var.access_mode == "unrestricted" :
      can(regex("^(\\d{1,3}\\.){3}\\d{1,3}/32$", var.tester_cidr))
      && can(cidrhost(var.tester_cidr, 0))
    )
    error_message = "tester_cidr should be a single IPv4 address with /32, such as 203.0.113.7/32."
  }
}

variable "access_mode" {
  description = "Application access over HTTPS: restricted allows tester_cidr only; unrestricted allows any caller IP. TCP 443 stays public for certificate validation in both modes."
  type        = string
  default     = "restricted"

  validation {
    condition     = contains(["restricted", "unrestricted"], var.access_mode)
    error_message = "access_mode must be restricted or unrestricted; both use HTTPS."
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

variable "app_settings" {
  description = "The app's settings, by their .env names, such as { GROUNDLINE_CHECK_TIMEOUT_SECONDS = 180 }. A setting left out uses the app's default. They reach the server as tags, which infra/server/apply writes into its .env; changing one updates the running server in place. modules/app-settings holds their rules."
  type        = map(number)
  default     = {}
}

variable "lifetime" {
  description = "disposable: for trying a commit; it terminates itself after auto_terminate_hours, and make aws-destroy deletes its data. lasting: for a deployment that keeps running, such as staging or production; it has no time limit, and make aws-destroy keeps its data and address unless DATA=delete."
  type        = string
  default     = "disposable"

  validation {
    condition     = contains(["disposable", "lasting"], var.lifetime)
    error_message = "lifetime must be disposable or lasting."
  }
  validation {
    condition     = var.lifetime != "lasting" || var.auto_terminate_hours == 0
    error_message = "A lasting deployment has no time limit: set auto_terminate_hours = 0."
  }
}

variable "budget_amount" {
  description = "The monthly budget alert's amount, in US dollars. It defaults to $5 for a disposable deployment, to catch a forgotten server, and $35 for a lasting one, about twice what it costs. The alert covers the whole AWS project, so a lasting deployment is best in a project of its own."
  type        = number
  default     = null

  validation {
    condition     = var.budget_amount == null || try(var.budget_amount > 0, false)
    error_message = "budget_amount must be more than 0."
  }
}
