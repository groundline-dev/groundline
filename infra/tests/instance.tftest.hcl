# The EC2 instance, its startup script and the outputs the scripts read.
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

run "the_server_requires_imdsv2_and_has_an_encrypted_disposable_tagged_disk" {
  command = plan

  assert {
    condition     = aws_instance.app.instance_type == "t4g.small"
    error_message = "The default instance type should be t4g.small."
  }
  assert {
    condition     = aws_instance.app.metadata_options[0].http_tokens == "required"
    error_message = "The instance should require IMDSv2 (http_tokens = required)."
  }
  assert {
    condition     = aws_instance.app.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "The metadata hop limit should be 1, so containers can't get the instance role's credentials."
  }
  assert {
    condition     = aws_instance.app.credit_specification[0].cpu_credits == "standard"
    error_message = "The instance should use standard CPU credits, so heavy CPU use slows it down instead of costing extra."
  }
  assert {
    condition = (
      aws_instance.app.root_block_device[0].encrypted == true
      && aws_instance.app.root_block_device[0].volume_type == "gp3"
      && aws_instance.app.root_block_device[0].volume_size == 20
    )
    error_message = "The root disk should be a 20 GB encrypted gp3 volume."
  }
  assert {
    condition     = aws_instance.app.root_block_device[0].delete_on_termination == true
    error_message = "The root disk should be deleted with the instance, so make aws-destroy leaves no volume."
  }
  assert {
    # The provider's default_tags don't reach the root disk; it needs its own.
    condition     = lookup(aws_instance.app.root_block_device[0].tags, "app", "") == "groundline"
    error_message = "The root disk should be tagged app=groundline."
  }
  assert {
    condition     = aws_instance.app.user_data_replace_on_change == true
    error_message = "Changing the startup script should replace the instance, since it runs only on first boot."
  }
}

run "the_server_and_its_disk_name_the_deployment" {
  command = plan

  assert {
    condition     = aws_instance.app.tags["Name"] == "groundline-alice"
    error_message = "The instance should be named groundline-<deployment_name>, so developers sharing an AWS project can tell servers apart."
  }
  assert {
    condition     = lookup(aws_instance.app.root_block_device[0].tags, "deployment", "") == "alice"
    error_message = "The root disk should be tagged deployment=<deployment_name>."
  }
  assert {
    condition     = startswith(aws_security_group.app.name_prefix, "groundline-alice-")
    error_message = "The security group's name should start with groundline-<deployment_name>-."
  }
}

run "the_server_terminates_itself_after_eight_hours_by_default" {
  command = plan

  assert {
    condition     = aws_instance.app.instance_initiated_shutdown_behavior == "terminate"
    error_message = "Shutting the server down from inside should terminate it, so the deadline leaves nothing running."
  }
  assert {
    # 8 hours in seconds, written to the deadline file a systemd timer checks.
    condition = (
      strcontains(aws_instance.app.user_data, "28800")
      && strcontains(aws_instance.app.user_data, "/var/lib/groundline/terminate-at")
      && can(regex("systemctl enable (--now )?groundline-expire.timer", aws_instance.app.user_data))
    )
    error_message = "The startup script should write a deadline 28800 seconds away to /var/lib/groundline/terminate-at and enable groundline-expire.timer."
  }
}

run "a_zero_deadline_leaves_the_server_running" {
  command = plan
  variables {
    auto_terminate_hours = 0
  }

  assert {
    # A long-lived deployment should keep its data if someone shuts it down from inside.
    condition     = aws_instance.app.instance_initiated_shutdown_behavior == "stop"
    error_message = "With auto_terminate_hours = 0, shutting the server down from inside should only stop it."
  }
  assert {
    condition     = !strcontains(aws_instance.app.user_data, "groundline-expire")
    error_message = "With auto_terminate_hours = 0, the startup script shouldn't set up groundline-expire.timer."
  }
}

run "a_negative_deadline_is_rejected" {
  command = plan
  variables {
    auto_terminate_hours = -1
  }
  expect_failures = [var.auto_terminate_hours]
}

run "a_fractional_deadline_is_rejected" {
  command = plan
  variables {
    auto_terminate_hours = 1.5
  }
  expect_failures = [var.auto_terminate_hours]
}

run "a_deployment_name_with_capitals_is_rejected" {
  command = plan
  variables {
    deployment_name = "Alice"
  }
  expect_failures = [var.deployment_name]
}

run "a_deployment_name_with_a_space_is_rejected" {
  command = plan
  variables {
    deployment_name = "alice smith"
  }
  expect_failures = [var.deployment_name]
}

run "a_deployment_name_over_twenty_characters_is_rejected" {
  command = plan
  variables {
    deployment_name = "abcdefghijklmnopqrstu"
  }
  expect_failures = [var.deployment_name]
}

run "the_startup_script_installs_docker_and_starts_the_pinned_revision" {
  command = plan

  assert {
    condition     = can(regex("dnf install[^\n]*docker", aws_instance.app.user_data)) && can(regex("dnf install[^\n]*git", aws_instance.app.user_data))
    error_message = "The startup script should install Docker and git with dnf."
  }
  assert {
    condition     = can(regex("sha256sum (-c|--check)", aws_instance.app.user_data)) && strcontains(aws_instance.app.user_data, "compose") && strcontains(aws_instance.app.user_data, "buildx")
    error_message = "The startup script should install Compose and buildx and check their sha256 checksums."
  }
  assert {
    # Docker starting at boot brings the containers back after a reboot (R11).
    condition     = can(regex("systemctl enable (--now )?docker", aws_instance.app.user_data))
    error_message = "The startup script should enable the Docker service so it starts at boot."
  }
  assert {
    condition     = strcontains(aws_instance.app.user_data, var.repository_url) && strcontains(aws_instance.app.user_data, var.git_ref) && can(regex("git[^\n]*checkout", aws_instance.app.user_data))
    error_message = "The startup script should clone repository_url and check out git_ref."
  }
  assert {
    condition     = strcontains(aws_instance.app.user_data, "APP_BIND_ADDRESS=0.0.0.0")
    error_message = "The startup script should set APP_BIND_ADDRESS=0.0.0.0 so the app is reachable from outside."
  }
  assert {
    condition     = can(regex("docker compose[^\n]* up (-d|--detach)", aws_instance.app.user_data))
    error_message = "The startup script should start the stack with docker compose up -d."
  }
  assert {
    condition     = strcontains(aws_instance.app.user_data, "/var/log/groundline-bootstrap.log")
    error_message = "The startup script should log to /var/log/groundline-bootstrap.log, which the smoke test prints on failure."
  }
}

run "the_startup_script_generates_both_database_passwords_in_hex" {
  command = plan

  assert {
    condition     = length(regexall("openssl rand -hex", aws_instance.app.user_data)) >= 2
    error_message = "The startup script should generate each Postgres password with openssl rand -hex."
  }
  assert {
    condition     = strcontains(aws_instance.app.user_data, "POSTGRES_PASSWORD=") && strcontains(aws_instance.app.user_data, "GROUNDLINE_DB_PASSWORD=")
    error_message = "The startup script should write POSTGRES_PASSWORD and GROUNDLINE_DB_PASSWORD to .env."
  }
  assert {
    condition     = can(regex("chmod 0?600", aws_instance.app.user_data))
    error_message = "The startup script should make .env readable only by its owner (chmod 600)."
  }
}

run "the_startup_script_reads_the_anthropic_key_from_parameter_store" {
  command = plan

  assert {
    condition     = strcontains(aws_instance.app.user_data, var.anthropic_parameter_name) && strcontains(aws_instance.app.user_data, "--with-decryption")
    error_message = "The startup script should read the Anthropic parameter with --with-decryption."
  }
  assert {
    condition     = strcontains(aws_instance.app.user_data, "ANTHROPIC_API_KEY=")
    error_message = "The startup script should write ANTHROPIC_API_KEY to .env."
  }
}

run "the_outputs_give_the_scripts_what_they_read" {
  # apply runs against the mock provider: nothing is created in AWS.
  override_resource {
    target = aws_instance.app
    values = { id = "i-0123456789abcdef0", public_ip = "203.0.113.10" }
  }
  override_resource {
    target = aws_security_group.app
    values = { id = "sg-0123456789abcdef0" }
  }

  assert {
    condition     = output.app_url == "http://203.0.113.10:8001"
    error_message = "app_url should be http://<public IP>:8001, got ${output.app_url}."
  }
  assert {
    condition     = output.instance_id == "i-0123456789abcdef0" && output.region == "eu-north-1"
    error_message = "instance_id and region should name the instance and the Region it runs in."
  }
  assert {
    # scripts/aws-destroy reads these before destroying, to check each is gone.
    condition = (
      output.security_group_id == "sg-0123456789abcdef0"
      && output.iam_role_name == aws_iam_role.instance.name
      && output.instance_profile_name == aws_iam_instance_profile.instance.name
      && output.budget_name == aws_budgets_budget.monthly.name
    )
    error_message = "The outputs should name the security group, IAM role, instance profile and budget."
  }
}
