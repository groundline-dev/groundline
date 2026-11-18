# What a deployment keeps when its server is replaced: the data volume and the
# public address. They have their own Terraform folder, so make aws-destroy can
# delete a lasting deployment's server and keep these. infra/main.tf attaches
# both to the server, finding them by their deployment and role tags.

# The default VPC's subnets, one for each Availability Zone, and the zones that
# offer the server's instance type. The server has to run in the volume's zone.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_subnet" "default" {
  for_each = toset(data.aws_subnets.default.ids)
  id       = each.value
}

data "aws_ec2_instance_type_offerings" "server" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

locals {
  # The first zone, in alphabetical order, with a default subnet and the
  # instance type, so the choice is the same on every plan.
  zones = sort(setintersection(
    toset([for subnet in data.aws_subnet.default : subnet.availability_zone]),
    toset(data.aws_ec2_instance_type_offerings.server.locations),
  ))
}

# The database, the HTTPS certificates and the database passwords. The server
# formats it on its first boot and mounts it before Docker starts
# (infra/bootstrap.sh.tftpl). The scripts refuse any plan that would delete or
# replace it, except when deleting the deployment's data is what was asked for.
resource "aws_ebs_volume" "data" {
  availability_zone = try(local.zones[0], null)
  size              = var.data_volume_gib
  type              = "gp3"
  encrypted         = true

  tags = {
    Name     = "groundline-${var.deployment_name}-data"
    role     = "data"
    lifetime = var.lifetime
  }

  lifecycle {
    # Choosing another zone later, say for another instance type, would replace
    # the volume and lose the data, so the zone it was made in stays.
    ignore_changes = [availability_zone]

    precondition {
      condition     = length(local.zones) > 0
      error_message = "No Availability Zone in this Region has both a default subnet and ${var.instance_type}. Choose another instance_type or Region."
    }
  }
}

# The address callers use. It moves to each new server, so it stays the same
# when the server is replaced.
resource "aws_eip" "app" {
  domain = "vpc"

  tags = {
    Name = "groundline-${var.deployment_name}"
    role = "address"
  }
}
