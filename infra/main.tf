# A deployment's server: an EC2 instance in the default VPC, its security
# group and its IAM role, with the data volume and the address from
# infra/data/ attached. bootstrap.sh.tftpl runs on its first boot.

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

# AWS's public parameter for the newest Amazon Linux 2023 arm64 AMI. It holds no
# secret. The Anthropic parameter is never read here, so the Anthropic API key
# stays out of state.
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  # Each deployment has its own Anthropic API key, which scripts/aws-up stores
  # under this name and scripts/aws-destroy deletes.
  anthropic_parameter_name = "/groundline/${var.deployment_name}/anthropic-api-key"
  anthropic_parameter_arn = join("", [
    "arn:${data.aws_partition.current.partition}:ssm:${var.region}:",
    "${data.aws_caller_identity.current.account_id}:parameter${local.anthropic_parameter_name}",
  ])
}

# --- The data volume and the address, from infra/data/ ---

# scripts/aws-deploy applies infra/data/ first, and this folder finds what it
# made by its tags. one() fails the plan if two match. It's null if none does,
# so make aws-destroy still works when they're gone; the server's
# precondition says what to do when they're missing for a new server.
data "aws_ebs_volumes" "data" {
  filter {
    name   = "tag:app"
    values = ["groundline"]
  }
  filter {
    name   = "tag:deployment"
    values = [var.deployment_name]
  }
  filter {
    name   = "tag:role"
    values = ["data"]
  }
}

data "aws_eips" "app" {
  filter {
    name   = "tag:app"
    values = ["groundline"]
  }
  filter {
    name   = "tag:deployment"
    values = [var.deployment_name]
  }
  filter {
    name   = "tag:role"
    values = ["address"]
  }
}

locals {
  data_volume_id    = one(data.aws_ebs_volumes.data.ids)
  eip_allocation_id = one(data.aws_eips.app.allocation_ids)
  public_ip         = one(data.aws_eips.app.public_ips)
}

data "aws_ebs_volume" "data" {
  count = local.data_volume_id == null ? 0 : 1
  filter {
    name   = "volume-id"
    values = [local.data_volume_id]
  }
}

# The server has to run in the volume's Availability Zone, so it goes in the
# default subnet there.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "app" {
  count             = length(data.aws_ebs_volume.data)
  vpc_id            = data.aws_vpc.default.id
  availability_zone = data.aws_ebs_volume.data[0].availability_zone
  default_for_az    = true
}

# --- Network: one entry point, selected explicitly by access_mode ---

resource "aws_security_group" "app" {
  name_prefix = "groundline-${var.deployment_name}-"
  description = "groundline HTTPS deployment: public TLS with application access controlled by Caddy"
}

resource "aws_vpc_security_group_ingress_rule" "app" {
  security_group_id = aws_security_group.app.id
  # TLS-ALPN certificate validation needs public 443 even in restricted mode.
  # Caddy restricts application requests by their actual source IP.
  description = "HTTPS and TLS-ALPN validation; Caddy enforces application access"
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
  cidr_ipv4   = "0.0.0.0/0"
}

# Terraform removes AWS's default outbound rule, so the server needs this one to
# reach dnf, GitHub, Docker Hub, Anthropic and SSM.
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.app.id
  description       = "all outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# --- IAM: SSM Run Command and the one Anthropic parameter (R4) ---

resource "aws_iam_role" "instance" {
  name        = "groundline-instance-${var.deployment_name}"
  description = "The server of a groundline test deployment: SSM Run Command and its Anthropic API key"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The SSM agent's actions, copied from AWS's managed policy for SSM-managed
# instances without ssm:GetParameter and ssm:GetParameters, which that policy
# grants on every parameter. The last statement allows reading only the
# Anthropic parameter.
resource "aws_iam_role_policy" "instance" {
  name = "groundline-instance-${var.deployment_name}"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid    = "SsmAgent"
        Effect = "Allow"
        Action = [
          "ssm:DescribeAssociation",
          "ssm:GetDeployablePatchSnapshotForInstance",
          "ssm:GetDocument",
          "ssm:DescribeDocument",
          "ssm:GetManifest",
          "ssm:ListAssociations",
          "ssm:ListInstanceAssociations",
          "ssm:PutInventory",
          "ssm:PutComplianceItems",
          "ssm:PutConfigurePackageResult",
          "ssm:UpdateAssociationStatus",
          "ssm:UpdateInstanceAssociationStatus",
          "ssm:UpdateInstanceInformation",
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel",
          "ec2messages:AcknowledgeMessage",
          "ec2messages:DeleteMessage",
          "ec2messages:FailMessage",
          "ec2messages:GetEndpoint",
          "ec2messages:GetMessages",
          "ec2messages:SendReply",
        ]
        Resource = "*"
      },
      {
        Sid      = "AnthropicApiKey"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [local.anthropic_parameter_arn]
      },
      ], [for statement in [
        # With a deadline, the server releases its Elastic IP just before it
        # terminates itself, since an address costs money even when no server
        # uses it. Only its own address: disassociating also names the network
        # interface it's on, which isn't known until the server exists.
        {
          Sid      = "FindAddressAtDeadline"
          Effect   = "Allow"
          Action   = ["ec2:DescribeAddresses"]
          Resource = "*"
        },
        {
          Sid    = "ReleaseAddressAtDeadline"
          Effect = "Allow"
          Action = ["ec2:DisassociateAddress", "ec2:ReleaseAddress"]
          Resource = [
            "arn:${data.aws_partition.current.partition}:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:elastic-ip/${coalesce(local.eip_allocation_id, "none")}",
            "arn:${data.aws_partition.current.partition}:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:network-interface/*",
          ]
        },
    ] : statement if var.auto_terminate_hours > 0 && local.eip_allocation_id != null])
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "groundline-instance-${var.deployment_name}"
  role = aws_iam_role.instance.name
}

# --- The server (R1, R3, R11) ---

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  subnet_id              = one(data.aws_subnet.app[*].id)
  vpc_security_group_ids = [aws_security_group.app.id]
  # The server needs an address of its own for the few seconds until its
  # Elastic IP is associated. AWS releases that one then.
  associate_public_ip_address = true

  # With a deadline, the startup script shuts the server down when it passes,
  # and this makes that shutdown terminate the server. Without one, a shutdown
  # from inside only stops it, so a long-lived deployment keeps its data.
  instance_initiated_shutdown_behavior = var.auto_terminate_hours > 0 ? "terminate" : "stop"

  # The startup script runs only on first boot, so a change to it needs a new
  # server. The commit and the access mode aren't in it: they're tags, which
  # the server reads, so a new commit or mode is applied in place, by
  # scripts/aws-deploy over SSM, and the server keeps running.
  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    region                 = var.region
    auto_terminate_seconds = var.auto_terminate_hours * 3600
    # The volume's device name, as /dev/disk/by-id names it on Nitro instances.
    data_device       = "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${replace(coalesce(local.data_volume_id, "none"), "-", "")}"
    eip_allocation_id = coalesce(local.eip_allocation_id, "none")
  })
  user_data_replace_on_change = true

  # A hop limit of 1 stops the metadata service's replies one hop out, so the
  # containers, which sit behind Docker's bridge network, can't get the role's
  # credentials. The SSM agent and the startup script run on the server itself.
  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  # T4g instances default to unlimited CPU credits, where CPU use above the
  # baseline costs extra with no ceiling. Standard credits slow the server down
  # instead, so its cost stays what the budget expects.
  credit_specification {
    cpu_credits = "standard"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
    # The provider's default_tags don't reach the root disk.
    tags = {
      app        = "groundline"
      deployment = var.deployment_name
    }
  }

  # What the server runs, who may reach it and the app's settings. The server
  # reads these through IMDS, so changing one updates the server in place
  # instead of replacing it. They contain no secrets.
  tags = merge({
    Name                      = "groundline-${var.deployment_name}"
    groundline-lifetime       = var.lifetime
    groundline-git-ref        = var.git_ref
    groundline-repository-url = var.repository_url
    groundline-access-mode    = var.access_mode
    groundline-allowed-cidr   = var.access_mode == "restricted" ? var.tester_cidr : "0.0.0.0/0"
    # The Elastic IP, which the server waits for before it starts, so it never
    # serves HTTPS on the address it has for its first few seconds.
    groundline-public-ip = coalesce(local.public_ip, "none")
    }, {
    for name, value in var.app_settings : "groundline-setting-${name}" => tostring(value)
  })

  lifecycle {
    # A newer AMI shouldn't replace a running server; the next deployment gets it.
    ignore_changes = [ami]

    precondition {
      condition     = local.data_volume_id != null && local.eip_allocation_id != null
      error_message = "This deployment has no data volume or Elastic IP: scripts/aws-deploy creates them with infra/data/ first. Run make aws-up."
    }
  }
}

# The data volume, as /dev/sdf. Stopping the server first lets Postgres shut
# down cleanly before the volume is detached, when the server is replaced or
# deleted. The count lets make aws-destroy delete a server whose volume or
# address is missing, such as one deployed before infra/data/ existed; any
# other plan without them fails the server's precondition.
resource "aws_volume_attachment" "data" {
  count                          = local.data_volume_id == null ? 0 : 1
  device_name                    = "/dev/sdf"
  volume_id                      = local.data_volume_id
  instance_id                    = aws_instance.app.id
  stop_instance_before_detaching = true
}

resource "aws_eip_association" "app" {
  count         = local.eip_allocation_id == null ? 0 : 1
  allocation_id = local.eip_allocation_id
  instance_id   = aws_instance.app.id
}
