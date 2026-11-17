# The data volume and the address a deployment keeps when its server is
# replaced. A mock AWS provider stands in for AWS, so these tests need no
# credentials and create nothing. Run with: terraform -chdir=infra/data test

mock_provider "aws" {
  mock_data "aws_vpc" {
    defaults = { id = "vpc-0123456789abcdef0" }
  }
  mock_data "aws_subnets" {
    defaults = { ids = ["subnet-0123456789abcdef0"] }
  }
  mock_data "aws_subnet" {
    defaults = { availability_zone = "eu-north-1b" }
  }
  mock_data "aws_ec2_instance_type_offerings" {
    defaults = { locations = ["eu-north-1a", "eu-north-1b", "eu-north-1c"] }
  }
}

variables {
  region          = "eu-north-1"
  deployment_name = "alice"
}

run "the_data_volume_is_a_small_encrypted_gp3_volume_named_for_the_deployment" {
  command = plan

  assert {
    condition = (
      aws_ebs_volume.data.encrypted == true
      && aws_ebs_volume.data.type == "gp3"
      && aws_ebs_volume.data.size == 5
    )
    error_message = "The data volume should be a 5 GiB encrypted gp3 volume by default."
  }
  assert {
    # infra/main.tf finds it by these tags.
    condition = (
      aws_ebs_volume.data.tags["role"] == "data"
      && aws_ebs_volume.data.tags["Name"] == "groundline-alice-data"
    )
    error_message = "The data volume should be tagged role=data and named groundline-<deployment_name>-data."
  }
}

run "the_volume_goes_in_a_zone_with_a_default_subnet_and_the_instance_type" {
  command = plan

  assert {
    # eu-north-1a offers the type but has no default subnet in the mock.
    condition     = aws_ebs_volume.data.availability_zone == "eu-north-1b"
    error_message = "The volume should be in the first zone that has both a default subnet and the instance type."
  }
}

run "no_suitable_zone_stops_the_plan" {
  command = plan
  override_data {
    target = data.aws_ec2_instance_type_offerings.server
    values = { locations = ["eu-north-1a"] }
  }
  expect_failures = [aws_ebs_volume.data]
}

run "the_address_is_an_elastic_ip_tagged_for_the_deployment" {
  command = plan

  assert {
    condition     = aws_eip.app.domain == "vpc" && aws_eip.app.tags["role"] == "address"
    error_message = "The address should be a VPC Elastic IP tagged role=address."
  }
}

run "the_volume_size_can_be_chosen" {
  command = plan
  variables {
    data_volume_gib = 20
  }

  assert {
    condition     = aws_ebs_volume.data.size == 20
    error_message = "data_volume_gib should set the volume's size."
  }
}

run "a_fractional_or_zero_volume_size_is_rejected" {
  command = plan
  variables {
    data_volume_gib = 0
  }
  expect_failures = [var.data_volume_gib]
}

run "the_outputs_give_the_scripts_what_they_read" {
  override_resource {
    target = aws_ebs_volume.data
    values = { id = "vol-0123456789abcdef0", availability_zone = "eu-north-1b", size = 5 }
  }
  override_resource {
    target = aws_eip.app
    values = { allocation_id = "eipalloc-0123456789abcdef0", public_ip = "203.0.113.10" }
  }

  assert {
    condition = (
      output.volume_id == "vol-0123456789abcdef0"
      && output.availability_zone == "eu-north-1b"
      && output.eip_allocation_id == "eipalloc-0123456789abcdef0"
      && output.public_ip == "203.0.113.10"
      && output.data_volume_gib == 5
    )
    error_message = "The outputs should name the volume, its zone and size, and the Elastic IP and its address."
  }
}
