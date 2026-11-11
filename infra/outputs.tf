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
  description = "The public HTTPS address, or tester-only HTTP address, selected by access_mode."
  value       = var.access_mode == "https" ? "https://${aws_instance.app.public_ip}" : "http://${aws_instance.app.public_ip}:8001"
}

output "access_mode" {
  value = var.access_mode
}

output "public_ip" {
  value = aws_instance.app.public_ip
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
