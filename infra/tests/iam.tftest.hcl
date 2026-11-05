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
}

variables {
  region             = "eu-north-1"
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

run "the_instance_role_can_read_only_the_anthropic_parameter" {
  command = plan

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      s.Effect == "Allow"
      && contains(flatten([s.Action]), "ssm:GetParameter")
      && flatten([s.Resource]) == ["arn:aws:ssm:eu-north-1:123456789012:parameter/groundline/anthropic-api-key"]
    ])
    error_message = "The role should be allowed ssm:GetParameter on the Anthropic parameter's ARN."
  }
  assert {
    # Any action that can read parameters applies only to that one ARN.
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      alltrue([
        for a in flatten([s.Action]) :
        !can(regex("^ssm:GetParameter", a))
        || flatten([s.Resource]) == ["arn:aws:ssm:eu-north-1:123456789012:parameter/groundline/anthropic-api-key"]
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
