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
    # From tags, so a new commit is an in-place upgrade, not a new server.
    condition = (
      aws_instance.app.tags["groundline-git-ref"] == var.git_ref
      && aws_instance.app.tags["groundline-repository-url"] == var.repository_url
      && !strcontains(aws_instance.app.user_data, var.git_ref)
      && strcontains(aws_instance.app.user_data, "tags/instance/groundline-git-ref")
      && can(regex("git[^\n]*checkout", aws_instance.app.user_data))
    )
    error_message = "The startup script should clone and check out the commit in the groundline-git-ref and groundline-repository-url tags, which aren't in user_data."
  }
  assert {
    # infra/server/apply writes .env and runs docker compose up, as each upgrade does.
    condition     = strcontains(aws_instance.app.user_data, "infra/server/apply")
    error_message = "The startup script should start the stack with the checkout's infra/server/apply."
  }
  assert {
    condition     = strcontains(aws_instance.app.user_data, "/var/log/groundline-bootstrap.log")
    error_message = "The startup script should log to /var/log/groundline-bootstrap.log, which the smoke test prints on failure."
  }
}

run "the_startup_script_keeps_secrets_out_of_user_data" {
  command = plan

  assert {
    # infra/server/apply makes the passwords on the data volume and reads the
    # Anthropic API key, so neither is in the startup script, which EC2 keeps.
    condition = (
      !strcontains(aws_instance.app.user_data, "openssl rand")
      && !strcontains(aws_instance.app.user_data, "--with-decryption")
      && !strcontains(aws_instance.app.user_data, "POSTGRES_PASSWORD")
    )
    error_message = "The startup script shouldn't make passwords or read the Anthropic API key; infra/server/apply does."
  }
}

run "the_startup_script_mounts_the_data_volume_before_docker_starts" {
  command = plan

  assert {
    condition     = strcontains(aws_instance.app.user_data, "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol0123456789abcdef0")
    error_message = "The startup script should find the data volume by its NVMe ID, from the volume's ID."
  }
  assert {
    # A volume with a filesystem keeps it; only a new one is formatted.
    condition     = can(regex("if ! blkid[^\n]*\n[^\n]*\n[^\n]*mkfs.xfs", aws_instance.app.user_data))
    error_message = "The startup script should format the data volume only if blkid finds no filesystem on it."
  }
  assert {
    condition = (
      strcontains(aws_instance.app.user_data, "/var/lib/docker/volumes none bind")
      && strcontains(aws_instance.app.user_data, "RequiresMountsFor=/var/lib/docker/volumes")
      && strcontains(aws_instance.app.user_data, "ExecStartPre=/opt/groundline/infra/https/refresh-address")
    )
    error_message = "Docker's volumes should be on the data volume, and Docker should need that mount to start."
  }
  assert {
    condition     = strcontains(aws_instance.app.user_data, "groundline-public-ip") && aws_instance.app.tags["groundline-public-ip"] == "203.0.113.10"
    error_message = "The server should wait for its Elastic IP, named in its groundline-public-ip tag, before downloading anything."
  }
}

run "the_server_runs_in_the_volume_zone_with_the_volume_and_address_attached" {
  command = plan

  assert {
    condition     = aws_instance.app.subnet_id == "subnet-0123456789abcdef0" && data.aws_subnet.app[0].availability_zone == "eu-north-1b"
    error_message = "The server should go in the default subnet of the data volume's Availability Zone."
  }
  assert {
    condition = (
      aws_volume_attachment.data.volume_id == "vol-0123456789abcdef0"
      && aws_volume_attachment.data.device_name == "/dev/sdf"
      && aws_volume_attachment.data.stop_instance_before_detaching == true
    )
    error_message = "The data volume should be attached as /dev/sdf, stopping the server before it's detached."
  }
  assert {
    condition     = aws_eip_association.app.allocation_id == "eipalloc-0123456789abcdef0"
    error_message = "The Elastic IP should be associated with the server."
  }
}

run "a_server_without_its_data_volume_is_refused" {
  command = plan
  override_data {
    target = data.aws_ebs_volumes.data
    values = { ids = [] }
  }
  expect_failures = [aws_instance.app]
}

run "a_server_without_its_elastic_ip_is_refused" {
  command = plan
  override_data {
    target = data.aws_eips.app
    values = { allocation_ids = [], public_ips = [] }
  }
  expect_failures = [aws_instance.app]
}

run "the_server_releases_its_elastic_ip_before_it_terminates_itself" {
  command = plan

  assert {
    condition = (
      strcontains(aws_instance.app.user_data, "release-address --region eu-north-1 --allocation-id eipalloc-0123456789abcdef0")
      && strcontains(aws_instance.app.user_data, "disassociate-address")
    )
    error_message = "With a deadline, the server should release its Elastic IP before it shuts down."
  }
}

run "the_outputs_give_the_scripts_what_they_read" {
  # apply runs against the mock provider: nothing is created in AWS.
  override_resource {
    target = aws_instance.app
    values = { id = "i-0123456789abcdef0", public_ip = "198.51.100.1" }
  }
  override_resource {
    target = aws_eip_association.app
    values = { public_ip = "203.0.113.10" }
  }
  override_resource {
    target = aws_security_group.app
    values = { id = "sg-0123456789abcdef0" }
  }

  assert {
    # The Elastic IP's address, not the one the server started with.
    condition     = output.app_url == "https://203.0.113.10" && output.public_ip == "203.0.113.10"
    error_message = "app_url should be https://<Elastic IP>, got ${output.app_url}."
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
      && output.anthropic_parameter_name == "/groundline/${var.deployment_name}/anthropic-api-key"
    )
    error_message = "The outputs should name the security group, IAM role, instance profile, budget and Anthropic parameter."
  }
  assert {
    # scripts/aws-up reads these to offer keeping a running server.
    condition     = output.git_ref == var.git_ref && output.repository_url == var.repository_url
    error_message = "git_ref and repository_url should say which commit the server runs, and where it's cloned from."
  }
}

run "https_discovers_the_ip_before_docker_and_keeps_disposable_storage" {
  variables {
    access_mode = "unrestricted"
  }
  override_resource {
    target = aws_instance.app
    values = { id = "i-0123456789abcdef0" }
  }
  override_resource {
    target = aws_eip_association.app
    values = { public_ip = "203.0.113.10" }
  }
  assert {
    condition = (
      output.app_url == "https://203.0.113.10"
      && output.access_mode == "unrestricted"
      && output.public_ip == "203.0.113.10"
    )
    error_message = "HTTPS outputs must identify the IP endpoint and mode."
  }
  assert {
    condition = (
      strcontains(aws_instance.app.user_data, "ExecStartPre=/opt/groundline/infra/https/refresh-address")
      && !strcontains(aws_instance.app.user_data, "APP_BIND_ADDRESS=0.0.0.0")
      && aws_instance.app.metadata_options[0].http_put_response_hop_limit == 1
      && aws_instance.app.root_block_device[0].delete_on_termination
      && aws_instance.app.instance_initiated_shutdown_behavior == "terminate"
    )
    error_message = "HTTPS must refresh its address before Docker starts, isolate app ports and preserve IMDS/disk/expiry behavior."
  }
}
