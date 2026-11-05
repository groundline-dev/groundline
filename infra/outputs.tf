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
  description = "The app's address. Only tester_cidr can reach it."
  value       = "http://${aws_instance.app.public_ip}:8001"
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

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}
