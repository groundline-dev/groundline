# What the server's IAM role can do: run SSM commands and read one parameter.
# A mock AWS provider stands in for AWS, so these tests need no credentials
# and create nothing. Run with: terraform -chdir=infra test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  # The data volume and the Elastic IP that infra/data/ makes.
  mock_data "aws_ebs_volumes" {
    defaults = { ids = ["vol-0123456789abcdef0"] }
  }
  mock_data "aws_ebs_volume" {
    defaults = { availability_zone = "eu-north-1b" }
  }
  mock_data "aws_eips" {
    defaults = { allocation_ids = ["eipalloc-0123456789abcdef0"], public_ips = ["203.0.113.10"] }
  }
  mock_data "aws_subnet" {
    defaults = { id = "subnet-0123456789abcdef0", availability_zone = "eu-north-1b" }
  }
}

variables {
  access_mode        = "restricted"
  region             = "eu-north-1"
  deployment_name    = "alice"
  budget_alert_email = "alerts@example.com"
  tester_cidr        = "203.0.113.7/32"
  git_ref            = "0123456789abcdef0123456789abcdef01234567"
}

run "the_instance_role_is_for_ec2" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.instance.assume_role_policy).Statement[0].Principal.Service == "ec2.amazonaws.com"
    error_message = "Only EC2 should be able to assume the instance role."
  }
}

run "the_role_and_instance_profile_are_named_after_the_deployment" {
  command = plan

  # IAM names are unique per account, so a fixed name would allow one deployment per AWS project.
  assert {
    condition = (
      aws_iam_role.instance.name == "groundline-instance-alice"
      && aws_iam_instance_profile.instance.name == "groundline-instance-alice"
    )
    error_message = "The IAM role and instance profile should be named groundline-instance-<deployment_name>."
  }
}

run "the_instance_role_can_read_only_the_anthropic_parameter" {
  command = plan

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      s.Effect == "Allow"
      && contains(flatten([s.Action]), "ssm:GetParameter")
      && flatten([s.Resource]) == ["arn:aws:ssm:eu-north-1:123456789012:parameter/groundline/alice/anthropic-api-key"]
    ])
    error_message = "The role should be allowed ssm:GetParameter on its deployment's Anthropic parameter, /groundline/alice/anthropic-api-key."
  }
  assert {
    # Any action that can read parameters applies only to that one ARN.
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      alltrue([
        for a in flatten([s.Action]) :
        !can(regex("^ssm:GetParameter", a))
        || flatten([s.Resource]) == ["arn:aws:ssm:eu-north-1:123456789012:parameter/groundline/alice/anthropic-api-key"]
      ])
    ])
    error_message = "No statement should let the role read any other parameter."
  }
  assert {
    condition = alltrue(flatten([
      for s in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      [for a in flatten([s.Action]) : !strcontains(a, "*")]
    ]))
    error_message = "The policy should list each action, with no wildcards such as ssm:* or ssm:GetParameter*."
  }
}

run "with_a_deadline_the_role_can_release_only_its_own_elastic_ip" {
  command = plan

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      s.Sid == "ReleaseAddressAtDeadline"
      && contains(flatten([s.Resource]), "arn:aws:ec2:eu-north-1:123456789012:elastic-ip/eipalloc-0123456789abcdef0")
      && length([for r in flatten([s.Resource]) : r if strcontains(r, "elastic-ip/")]) == 1
    ])
    error_message = "With a deadline, the role should be able to release only the deployment's own Elastic IP."
  }
}

run "without_a_deadline_the_role_cannot_release_the_address" {
  command = plan
  variables {
    auto_terminate_hours = 0
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      !contains(flatten([s.Action]), "ec2:ReleaseAddress") && !contains(flatten([s.Action]), "ec2:DescribeAddresses")
    ])
    error_message = "With no deadline, nothing releases the Elastic IP, so the role shouldn't be allowed to."
  }
}
