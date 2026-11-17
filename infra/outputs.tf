# What the scripts in scripts/ read with `terraform -chdir=infra output -raw NAME`.

output "instance_id" {
  description = "The EC2 instance, for SSM Run Command."
  value       = aws_instance.app.id
}

output "region" {
  description = "The Region the server runs in."
  value       = var.region
}

output "app_url" {
  description = "The HTTPS address; access_mode controls application access rather than transport."
  value       = one([for association in aws_eip_association.app : "https://${association.public_ip}"])
}

output "access_mode" {
  value = var.access_mode
}

output "allowed_cidr" {
  description = "The source IP range Caddy allows for application requests. TCP/TLS 443 stays public."
  value       = var.access_mode == "restricted" ? var.tester_cidr : "0.0.0.0/0"
}

output "public_ip" {
  description = "The deployment's Elastic IP, from infra/data/."
  value       = one(aws_eip_association.app[*].public_ip)
}

# scripts/aws-destroy reads these before destroying, then checks each is gone.

output "security_group_id" {
  value = aws_security_group.app.id
}

output "iam_role_name" {
  value = aws_iam_role.instance.name
}

output "instance_profile_name" {
  value = aws_iam_instance_profile.instance.name
}

output "anthropic_parameter_name" {
  value = local.anthropic_parameter_name
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}

output "auto_terminate_hours" {
  description = "How many hours after its first boot the server terminates itself; 0 means never. scripts/aws-deploy prints it, and scripts/aws-reset-deadline caps a new deadline at it."
  value       = var.auto_terminate_hours
}

output "git_ref" {
  description = "The commit the server runs. scripts/aws-up reads it to offer keeping a running server."
  value       = var.git_ref
}

output "repository_url" {
  description = "The repository the server clones git_ref from."
  value       = var.repository_url
}

output "app_settings" {
  description = "The app settings the server has, besides the app's defaults. scripts/aws-deploy lists them."
  value       = var.app_settings
}

output "lifetime" {
  description = "disposable or lasting. scripts/aws-destroy keeps a lasting deployment's data."
  value       = var.lifetime
}

output "budget_amount" {
  description = "The monthly budget alert's amount, in US dollars."
  value       = local.budget_amount
}
