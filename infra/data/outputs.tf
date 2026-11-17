# What the scripts in scripts/ read with `terraform -chdir=infra/data output -raw NAME`.

output "volume_id" {
  description = "The data volume, which scripts/aws-destroy checks is gone."
  value       = aws_ebs_volume.data.id
}

output "availability_zone" {
  description = "The zone the volume is in, and so the zone the server runs in."
  value       = aws_ebs_volume.data.availability_zone
}

output "eip_allocation_id" {
  description = "The Elastic IP, which scripts/aws-destroy checks is gone."
  value       = aws_eip.app.allocation_id
}

output "public_ip" {
  description = "The deployment's address."
  value       = aws_eip.app.public_ip
}

output "data_volume_gib" {
  description = "The data volume's size, for the cost the scripts print."
  value       = aws_ebs_volume.data.size
}
