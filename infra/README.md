# infra

This folder holds Terraform for disposable groundline deployments on one EC2 instance. It supports public IP HTTPS and the existing tester-only HTTP mode. To deploy a server, follow [docs/deploying-to-aws.md](../docs/deploying-to-aws.md).

- [main.tf](main.tf): the server, its security group and its IAM role
- [budget.tf](budget.tf): the monthly budget alert
- [bootstrap.sh.tftpl](bootstrap.sh.tftpl): the startup script the server runs on its first boot
- [variables.tf](variables.tf), [outputs.tf](outputs.tf) and [versions.tf](versions.tf): the inputs, the values `scripts/aws-*` read, and the pinned versions
- [https/](https/): Caddy configuration and host IMDSv2 address renderer
- [tests/](tests/): the Terraform tests

## Why each setting is what it is

A test deployment holds only test data ([ADR 0006](../docs/adr/0006-no-real-patient-data-before-production.md)) and is meant to be thrown away. Both access modes preserve the disposable lifecycle and existing secret boundaries.

- **The default VPC:** every new AWS project has one, so a developer doesn't need to create or pay for a network. If someone deleted theirs, `aws ec2 create-default-vpc` brings it back.
- **One explicit access mode:** `tester_http` retains TCP 8001 from one `/32`; `https` exposes only TCP 443 to IPv4 callers. Terraform defaults to the former for old configurations, while new wizard configurations recommend and save HTTPS. The ingress resource retains its address. The security group's historical description is kept to avoid replacing it on an unchanged HTTP upgrade; the ingress rule description identifies the actual mode.
- **Native public IP TLS:** `compose.https.yaml` removes app and database port publications with `!reset` (Compose 2.24.4+). Caddy 2.11.9 is pinned by multi-platform image digest. The explicit Let's Encrypt issuer, `shortlived` profile, disabled HTTP challenge/redirects, and `default_sni` are required: a bare IP with Caddy defaults can otherwise use its internal CA. h1/h2 use TCP 443; no UDP or IPv6 ingress is added.
- **Host address reconciliation:** bootstrap saves `COMPOSE_FILE` in the root-only `.env`, so SSM key/diagnostic commands use the same override. `docker.service.d/groundline-https.conf` invokes `https/refresh-address` before Docker starts. That helper uses host IMDSv2, validates IPv4, and atomically renders a directory-mounted Caddyfile. Failure blocks Docker startup. ACME account/certificates stay in their own named volume; the proxy gets no app secrets. EIP provisioning, retained disks and custom domains remain out of scope.
- **SSM Run Command instead of SSH:** the scripts reach the server through SSM, so there's no key pair to manage and no open port 22.
- **The IAM role's policy is written out instead of attaching `AmazonSSMManagedInstanceCore`:** that managed policy also grants `ssm:GetParameter` and `ssm:GetParameters` on every parameter in the project. Our copy keeps the SSM agent's actions and allows reading only the deployment's own Anthropic API key, `/groundline/<deployment_name>/anthropic-api-key`.
- **Each deployment has its own Anthropic API key, kept in Parameter Store:** only the server reads it, when it starts. `make aws-up` stores it with the AWS CLI and `make aws-destroy` deletes it, and Terraform and the other scripts only check that it exists, so the Anthropic API key never lands in Terraform state or on a developer's screen. Everyone with access to the AWS project can still read it, which is why the docs recommend a project of your own, or in a shared project, a key from an Anthropic Console workspace with a low spend limit.
- **IMDSv2 with a hop limit of 1:** every metadata request needs a session token, and the reply that carries the token doesn't get past Docker's bridge network, so the containers can't get the role's credentials. The SSM agent and the startup script run on the server itself and still can. Nothing in the containers uses AWS.
- **Standard CPU credits:** T4g instances default to unlimited credits, which bill CPU use above the baseline at about $0.04 per vCPU-hour with no ceiling. Standard credits slow the server down instead, so it costs what the budget expects.
- **An encrypted 20 GB gp3 disk that's deleted on termination:** the data goes with the server, and the provider's `default_tags` don't reach the root disk, so it's tagged separately.
- **The 8-hour limit:** the startup script writes a deadline and installs a systemd timer that shuts the server down once it passes. `instance_initiated_shutdown_behavior = "terminate"` makes that shutdown delete the server. The deadline is written before anything else, so a server whose startup fails still goes away. With `auto_terminate_hours = 0` there's no timer, and a shutdown only stops the server, so it keeps its data.
- **A $5 budget for the whole project, without credits:** filtering by the `app` tag needs an account-level billing setting, so the budget covers everything in the project. Credits are left out because on the Free plan they bring the cost to $0, and the alert would never fire.
- **Names and tags from `deployment_name`:** IAM role and budget names are unique per account, so this is what lets several developers share one AWS project.
- **Their own AWS CLI profile, and a Terraform workspace for each AWS project:** the scripts sign in as `groundline-deploy`, which `make aws-up` and `make aws-project` sign in to the project chosen in the browser, and set `TF_WORKSPACE` to the account ID, so each project's state is separate and a developer can have a server in each at once. No account ID is written into the repository.
- **The server clones the code without signing in:** `main` comes from groundline-dev/groundline, and a branch from whichever GitHub remote has it, such as a fork. `scripts/aws-deploy` passes that remote's HTTPS address as `repository_url`, and checks that it can be cloned without signing in.

## Costs

The server costs about $0.024 an hour in us-east-1, about $17 a month: $0.0168 for the `t4g.small` instance, $0.005 for its public IP and $0.002 for its 20 GB disk. Prices differ a little between Regions. A server that runs to its 8-hour limit costs about $0.19. The security group, IAM role, instance profile and budget are free. The budget alert emails everyone who has deployed when the project's costs pass $2.50 and $5 in a month. `scripts/aws-common.sh` quotes the hourly price in the summary it shows before `make aws-deploy` changes anything, so update it there too.

## What needs updating

- **Caddy:** review updates to its digest in `compose.https.yaml` and run `make https-smoke` before accepting them. Test fixture Pebble/Python digests live in `tests/https_smoke.py`; update them deliberately with the same lifecycle test.
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

`make https-smoke` runs real Caddy and Pebble in isolated Docker containers: TLS-ALPN IP issuance, NAT/no-SNI selection, trust/identity rejection, retained certificates on restart, automatic renewal with a changed served fingerprint, a response after 125 seconds of silence, and CA-outage expiry/recovery. Only the fixture trusts its test root; it never contacts a public CA. It removes its containers/network/volume on completion. CI runs it separately. This proves the lifecycle, not public trust: `make aws-smoke` must still pass against a fresh EC2 endpoint with ordinary trust stores.

`tests/test_https_bootstrap.py` executes the renderer against fake IMDS responses. `tests/test_aws_access_modes.py` exercises first setup, remembered and missing modes, invalid settings, cancellation and keeping an existing server. These tests reach no AWS services.

### Repeatable HTTPS lifecycle checks

Use `make aws-https-smoke` on an existing HTTPS deployment. `RESTART=caddy` adds a confirmed proxy restart; `RESTART=host` additionally reboots EC2. The orchestration lives in `scripts/aws-https-smoke-test`, reuses `aws-smoke-test`, and obtains structured TLS evidence through `scripts/https_check.py --json`. Its bounded SSM path is in `scripts/aws-common.sh`. `tests/test_aws_https_smoke.py` covers target/preflight failures, cancellation, recovery, boot identity, certificate storage and expiry without reaching AWS. See the [operator guide](../docs/deploying-to-aws.md#testing-https-restart-recovery) for interruption and timeout behavior.

### Testing `make aws-up`

After changing the wizard or the scripts it runs, test it as a new developer would, from the repository root. You run it with your own settings moved aside, so it asks every question, then delete what it created. The server costs about $0.024 an hour while it runs.

1. Move your settings file aside, so the wizard asks for your deployment name and email address. Git ignores the copy, since it holds your email address:

   ```sh
   mv infra/terraform.tfvars infra/terraform.tfvars.bak
   ```

2. Run the wizard:

   ```sh
   make aws-up
   ```

3. Answer its questions:
   - **AWS project:** check that it shows the project's name and account ID, then answer `n` to choose a project.
   - **Signing in to the project:** check that the wizard waits for Enter before it asks for the Region.
   - **Region code:** first type something that isn't a Region code, such as `Ohio`, which it should reject, then paste your Region's code.
   - **Browser:** check that the wizard waits for Enter before opening it, and that it prints only the sign-in link. Choose the session of the project you signed in to.
   - **Deployment name and email address:** enter the same ones as before, so your resources keep their names.
   - **Anthropic API key:** check that the warning names the project and that it offers the key from `.env`, showing only its last 4 characters.
   - **Code:** choose your branch, to test finding the remote that has it.
   - **Terraform plan summary:** check that it says what you expect, then choose 1 to go ahead.
4. Wait for the smoke test, about 5 to 10 minutes. Read each step's output as a newcomer would: anything unclear, any long wait with no sign of progress, or any surprising question is worth fixing.
5. Compare the new settings file with your old one, then delete the old one. `diff` prints only the lines that differ, so `deployment_name` and `budget_alert_email` appear only if they changed or moved; the comments may differ too.

   ```sh
   diff infra/terraform.tfvars.bak infra/terraform.tfvars
   rm infra/terraform.tfvars.bak
   ```

6. Delete the server, and choose 1 when it asks. Check that it deletes your Anthropic API key's parameter:

   ```sh
   make aws-destroy
   ```

[tests/test_aws_scripts.py](../tests/test_aws_scripts.py) runs the smoke test, status, reset-deadline and destroy scripts against stand-in `aws` and `terraform` commands, with additional wizard and deploy coverage in `tests/test_aws_access_modes.py`; `aws-key` still needs a live check, and only this run shows what a person sees.
