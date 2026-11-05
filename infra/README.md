# infra

This folder holds the Terraform for groundline's AWS test deployments: one EC2 instance per developer that runs the Compose stack at a chosen commit. It's for whoever maintains the deployment. To deploy a server, follow "Deploying to AWS" in [DEVELOPMENT.md](../DEVELOPMENT.md) instead.

- [main.tf](main.tf): the server, its security group and its IAM role
- [budget.tf](budget.tf): the monthly budget alert
- [bootstrap.sh.tftpl](bootstrap.sh.tftpl): the startup script the server runs on its first boot
- [variables.tf](variables.tf), [outputs.tf](outputs.tf) and [versions.tf](versions.tf): the inputs, the values `scripts/aws-*` read, and the pinned versions
- [tests/](tests/): the Terraform tests

## Why each setting is what it is

A test deployment holds only test data ([ADR 0006](../docs/adr/0006-no-real-patient-data-before-production.md)) and is meant to be thrown away. Most settings below keep it cheap, reachable only by its developer, and unable to leak the Anthropic API key.

- **The default VPC:** every new AWS project has one, so a developer doesn't need to create or pay for a network. If someone deleted theirs, `aws ec2 create-default-vpc` brings it back.
- **The security group admits one `/32` on port 8001:** the app speaks plain HTTP until ALL-90 adds HTTPS, so only the developer's own IP can reach it. The variable's validation rejects wider ranges, and there's no inbound rule for SSH.
- **SSM Run Command instead of SSH:** the scripts reach the server through SSM, so there's no key pair to manage and no open port 22.
- **The IAM role's policy is written out instead of attaching `AmazonSSMManagedInstanceCore`:** that managed policy also grants `ssm:GetParameter` and `ssm:GetParameters` on every parameter in the project. Our copy keeps the SSM agent's actions and allows reading only `/groundline/anthropic-api-key`.
- **IMDSv2 with a hop limit of 1:** every metadata request needs a session token, and the reply that carries the token doesn't get past Docker's bridge network, so the containers can't get the role's credentials. The SSM agent and the startup script run on the server itself and still can. Nothing in the containers uses AWS.
- **Standard CPU credits:** T4g instances default to unlimited credits, which bill CPU use above the baseline at about $0.04 per vCPU-hour with no ceiling. Standard credits slow the server down instead, so it costs what the budget expects.
- **An encrypted 20 GB gp3 disk that's deleted on termination:** the data goes with the server, and the provider's `default_tags` don't reach the root disk, so it's tagged separately.
- **The 8-hour limit:** the startup script writes a deadline and installs a systemd timer that shuts the server down once it passes. `instance_initiated_shutdown_behavior = "terminate"` makes that shutdown delete the server. The deadline is written before anything else, so a server whose startup fails still goes away. With `auto_terminate_hours = 0` there's no timer, and a shutdown only stops the server, so it keeps its data.
- **A $5 budget for the whole project, without credits:** filtering by the `app` tag needs an account-level billing setting, so the budget covers everything in the project. Credits are left out because on the Free plan they bring the cost to $0, and the alert would never fire.
- **Names and tags from `deployment_name`:** IAM role and budget names are unique per account, so this is what lets several developers share one AWS project.

## What needs updating

- **Compose and buildx:** `bootstrap.sh.tftpl` downloads both at pinned versions and checks each file's sha256, since Amazon Linux doesn't package them. To update one, change its URL and its sha256 in the `install_plugin` call, taking the new one from the release's checksums file.
- **Terraform and the AWS provider:** `versions.tf` pins both with `~>`. Raise the pin, run `terraform -chdir=infra init -upgrade`, then `make infra-check`, and commit the updated `.terraform.lock.hcl`.
- **The SSM agent's actions:** our policy copies `AmazonSSMManagedInstanceCore` by hand. If AWS adds actions to that policy and Run Command stops working, compare the two.
- **The Anthropic parameter's key:** the role has no `kms:Decrypt`. Reading the parameter works only because it uses the default `aws/ssm` key. Moving it to a customer managed key breaks startup until the role gets `kms:Decrypt` on that key.
- **The AMI:** each new server gets the newest Amazon Linux 2023 arm64 AMI. `ignore_changes = [ami]` keeps a newer AMI from replacing a running server, so a running server keeps the AMI it started with.

## State

Terraform state stays in this folder (`terraform.tfstate`, which git ignores). There's no backend and no locking, since each deployment has one owner and is disposable. As a result, only the clone that created a server can update or destroy it. If test deployments ever need a second owner, such as a shared staging server, we can move state to S3 with a backend block and `terraform init -migrate-state`. `tests/test_infra.py` fails on a backend block, so that test changes too.

## Tests

`make infra-check` runs `terraform fmt`, `validate` and `test`. The tests plan and apply against a mock AWS provider, so they need Terraform but no AWS credentials, and CI runs them. Each file covers one part:

- `network.tftest.hcl`: the one inbound rule and the `tester_cidr` validation
- `iam.tftest.hcl`: the role's trust policy, its names, and that it can read only the Anthropic parameter
- `instance.tftest.hcl`: IMDSv2 and the hop limit, CPU credits, the disk, the time limit, the `deployment_name` validation, the startup script and the outputs
- `budget.tftest.hcl`: the amount, the thresholds and leaving out credits

The mock provider doesn't apply `default_tags` and can't list every resource of a kind, so [tests/test_infra.py](../tests/test_infra.py), which `make check` runs, reads the files instead. Among other things, it checks that every resource is tagged, that no Region or backend is written in, that the role gets no managed policy, that the security group has one inbound rule, and that only the startup script reads the Anthropic API key.

None of these tests reach AWS. `make aws-smoke` checks a real server.
