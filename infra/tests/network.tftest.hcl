# Who can reach the server: only the tester's IP, on the app's port.
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
  deployment_name    = "alice"
  budget_alert_email = "alerts@example.com"
  tester_cidr        = "203.0.113.7/32"
  git_ref            = "0123456789abcdef0123456789abcdef01234567"
}

run "only_the_tester_reaches_the_app_port" {
  command = plan

  # tests/test_infra.py checks that this is the only inbound rule.
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.app.ip_protocol == "tcp"
      && aws_vpc_security_group_ingress_rule.app.from_port == 8001
      && aws_vpc_security_group_ingress_rule.app.to_port == 8001
    )
    error_message = "The inbound rule should open only TCP port 8001."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.app.cidr_ipv4 == "203.0.113.7/32"
    error_message = "The inbound rule should allow only tester_cidr."
  }
}

run "the_whole_internet_is_rejected_as_tester_cidr" {
  command = plan
  variables {
    tester_cidr = "0.0.0.0/0"
  }
  expect_failures = [var.tester_cidr]
}

run "a_subnet_is_rejected_as_tester_cidr" {
  command = plan
  variables {
    tester_cidr = "203.0.113.0/24"
  }
  expect_failures = [var.tester_cidr]
}

run "https_opens_only_tcp_443_to_ipv4_callers" {
  command = plan
  variables {
    access_mode = "https"
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.app.ip_protocol == "tcp"
      && aws_vpc_security_group_ingress_rule.app.from_port == 443
      && aws_vpc_security_group_ingress_rule.app.to_port == 443
      && aws_vpc_security_group_ingress_rule.app.cidr_ipv4 == "0.0.0.0/0"
    )
    error_message = "HTTPS must expose only TCP 443."
  }
}

run "unknown_access_modes_are_rejected" {
  command = plan
  variables {
    access_mode = "public_http"
  }
  expect_failures = [var.access_mode]
}
