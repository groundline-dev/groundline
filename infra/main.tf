# The test server: an EC2 instance in the default VPC, its security group and
# its IAM role. bootstrap.sh.tftpl runs on its first boot.

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

# AWS's public parameter for the newest Amazon Linux 2023 arm64 AMI. It holds no
# secret; the Anthropic parameter is never read here, so the key stays out of state.
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  anthropic_parameter_arn = join("", [
    "arn:${data.aws_partition.current.partition}:ssm:${var.region}:",
    "${data.aws_caller_identity.current.account_id}:parameter${var.anthropic_parameter_name}",
  ])
}

# --- Network: only the tester's IP reaches the app's port (R6) ---

resource "aws_security_group" "app" {
  name_prefix = "groundline-"
  description = "groundline test server: the app port from the tester's IP only"
}

resource "aws_vpc_security_group_ingress_rule" "app" {
  security_group_id = aws_security_group.app.id
  description       = "groundline app from the tester"
  ip_protocol       = "tcp"
  from_port         = 8001
  to_port           = 8001
  cidr_ipv4         = var.tester_cidr
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
  name        = "groundline-instance"
  description = "The groundline test server: SSM Run Command and its Anthropic API key"

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
  name = "groundline-instance"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
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
    ]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "groundline-instance"
  role = aws_iam_role.instance.name
}

# --- The server (R1, R3, R11) ---

resource "aws_instance" "app" {
  ami                         = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type               = var.instance_type
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  vpc_security_group_ids      = [aws_security_group.app.id]
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    region                   = var.region
    repository_url           = var.repository_url
    git_ref                  = var.git_ref
    anthropic_parameter_name = var.anthropic_parameter_name
  })
  # The startup script runs only on first boot, so a change needs a new server.
  user_data_replace_on_change = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
    # The provider's default_tags don't reach the root disk.
    tags = {
      project = "groundline"
    }
  }

  tags = {
    Name = "groundline"
  }

  lifecycle {
    # A newer AMI shouldn't replace a running server; the next deployment gets it.
    ignore_changes = [ami]
  }
}
