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
  access_mode        = "restricted"
  region             = "eu-north-1"
  deployment_name    = "alice"
  budget_alert_email = "alerts@example.com"
  tester_cidr        = "203.0.113.7/32"
  git_ref            = "0123456789abcdef0123456789abcdef01234567"
}

run "restricted_keeps_tls_validation_public" {
  command = plan

  # tests/test_infra.py checks that this is the only inbound rule.
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.app.ip_protocol == "tcp"
      && aws_vpc_security_group_ingress_rule.app.from_port == 443
      && aws_vpc_security_group_ingress_rule.app.to_port == 443
    )
    error_message = "The inbound rule should open only TCP port 443."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.app.cidr_ipv4 == "0.0.0.0/0"
    error_message = "TLS must remain public so certificate issuance and renewal work."
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
    access_mode = "unrestricted"
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

run "restricted_requires_a_tester_address" {
  command = plan
  variables {
    tester_cidr = null
  }
  expect_failures = [var.tester_cidr]
}

run "unrestricted_needs_no_tester_address" {
  command = plan
  variables {
    access_mode = "unrestricted"
    tester_cidr = null
  }
}

run "restricted_metadata_keeps_the_allowed_ip_out_of_replacement_data" {
  command = plan
  assert {
    condition = (
      aws_instance.app.metadata_options[0].instance_metadata_tags == "enabled"
      && aws_instance.app.tags["groundline-access-mode"] == "restricted"
      && aws_instance.app.tags["groundline-allowed-cidr"] == var.tester_cidr
      && output.allowed_cidr == var.tester_cidr
      && !strcontains(aws_instance.app.user_data, var.tester_cidr)
      && strcontains(aws_instance.app.user_data, "restricted HTTPS application access")
      && aws_instance.app.user_data_replace_on_change
    )
    error_message = "Changing the tester IP must update metadata rather than replacement-triggering user_data; changing modes must change user_data."
  }
}

run "unrestricted_metadata_explicitly_allows_all_ipv4_callers" {
  command = plan
  variables { access_mode = "unrestricted" }
  assert {
    condition = (
      aws_instance.app.tags["groundline-access-mode"] == "unrestricted"
      && aws_instance.app.tags["groundline-allowed-cidr"] == "0.0.0.0/0"
      && output.allowed_cidr == "0.0.0.0/0"
      && strcontains(aws_instance.app.user_data, "unrestricted HTTPS application access")
    )
    error_message = "Unrestricted access must be explicit and distinct from restricted access."
  }
}
