# infra

This folder holds Terraform for groundline deployments on one EC2 instance, disposable or lasting. Both access modes use public-IP HTTPS; restricted mode limits application requests to the developer's public IP, while unrestricted mode admits any caller IP. To deploy a server, follow [docs/deploying-to-aws.md](../docs/deploying-to-aws.md).

- [main.tf](main.tf): the server, its security group and its IAM role, with the data volume and Elastic IP attached
- [budget.tf](budget.tf): the monthly budget alert
- [bootstrap.sh.tftpl](bootstrap.sh.tftpl): the startup script the server runs on its first boot
- [variables.tf](variables.tf), [outputs.tf](outputs.tf) and [versions.tf](versions.tf): the inputs, the values `scripts/aws-*` read, and the pinned versions
- [data/](data/): a second Terraform folder for what outlives the server: the data volume and the Elastic IP
- [server/apply](server/apply): what the server runs on its first boot and on every upgrade, to write `.env` and start the stack
- [https/](https/): Caddy configuration and host IMDSv2 address renderer
- [tests/](tests/): the Terraform tests ([data/tests/](data/tests/) for `data/`)

## Why each setting is what it is

Every deployment holds only synthetic test data ([ADR 0006](../docs/adr/0006-no-real-patient-data-before-production.md)). A disposable one is meant to be thrown away; a lasting one keeps its data and address across upgrades until it's deleted ([ADR 0019](../docs/adr/0019-aws-deployments-upgrade-in-place.md)). Both access modes keep the existing secret boundaries.

- **The default VPC:** every new AWS project has one, so a developer doesn't need to create or pay for a network. If someone deleted theirs, `aws ec2 create-default-vpc` brings it back.
- **One explicit access mode:** `restricted` (default) and `unrestricted` both expose only TCP 443 to IPv4 callers. TLS-ALPN validation stays publicly reachable in both modes. Caddy's `remote_ip` matcher restricts every application request in restricted mode, including health and docs, using the socket peer rather than supplied forwarded headers. Protected API endpoints need API keys in either mode. Missing settings use restricted HTTPS. The wizard asks the operator to select a supported mode for an unrecognized value; direct deployments reject that value with instructions. Terraform itself validates supported values.
- **Native public IP TLS:** `compose.https.yaml` removes app and database port publications with `!reset` (Compose 2.24.4+). Caddy 2.11.9 is pinned by multi-platform image digest. The explicit Let's Encrypt issuer, `shortlived` profile, disabled HTTP challenge/redirects, and `default_sni` are required: a bare IP with Caddy defaults can otherwise use its internal CA. h1/h2 use TCP 443; no UDP or IPv6 ingress is added.
- **Consistent Compose configuration:** `server/apply` saves `COMPOSE_FILE` in the root-only `.env`, so SSM commands for API keys and diagnostics use the same override.
- **Host address reconciliation:** `docker.service.d/groundline-https.conf` invokes `https/refresh-address` before Docker starts. The helper uses host IMDSv2, validates IPv4 and the access-mode and allowed-CIDR tags, and atomically renders a directory-mounted Caddyfile. Instance metadata tags are enabled for these non-secret settings. Failure blocks Docker startup.
- **What the server runs is in tags, not the startup script:** the commit, the repository, the access mode, the allowed IP, the lifetime and each app setting (`groundline-setting-<NAME>`) are instance tags, which the server reads through IMDS. Changing one updates the tags in place, and `scripts/aws-deploy` then upgrades the running server over SSM: it checks out the commit and runs that commit's `server/apply`, then reloads Caddy. Only a change to the startup script itself, such as `auto_terminate_hours`, replaces the server.
- **A data volume and an Elastic IP that outlive the server:** `data/` makes them, in their own Terraform state, so `make aws-destroy` can delete a lasting deployment's server and keep them. `main.tf` finds them by their `deployment` and `role` tags, runs the server in the volume's Availability Zone, attaches the volume as `/dev/sdf` (stopping the server first when it's detached, so Postgres shuts down cleanly) and associates the address. The startup script waits for the Elastic IP before it downloads anything, mounts the volume (formatting it only if it's new) and bind-mounts it over `/var/lib/docker/volumes` before Docker starts, so Postgres, the certificates and Docker's volume metadata live on it. Docker requires that mount, so a missing volume never gets an empty database in its place. The scripts refuse any plan that would delete or replace the volume or the address, except a disposable deployment's `make aws-destroy` or `DATA=delete`; Terraform's `prevent_destroy` can't be switched off for those, so the guard is in `scripts/aws-common.sh`. `ignore_changes = [availability_zone]` keeps a later change of instance type from moving, and so replacing, the volume.
- **Separate certificate storage:** ACME accounts and certificates stay in their own named volume, on the data volume; the proxy gets no app secrets.
- **Database passwords on the data volume:** `server/apply` makes them once and reuses them, since Postgres applies `POSTGRES_PASSWORD` only when it creates its database.
- **A disposable deployment leaves nothing that costs money:** `scripts/aws-deploy` marks its data volume to be deleted when the server terminates, and the server releases its Elastic IP just before it terminates itself at its deadline. That needs the only EC2 permissions the role has: describing addresses, and disassociating and releasing its own address. A lasting deployment's volume is never deleted with its server, and its role has neither permission.
- **SSM Run Command instead of SSH:** the scripts reach the server through SSM, so there's no key pair to manage and no open port 22.
- **The IAM role's policy is written out instead of attaching `AmazonSSMManagedInstanceCore`:** that managed policy also grants `ssm:GetParameter` and `ssm:GetParameters` on every parameter in the project. Our copy keeps the SSM agent's actions and allows reading only the deployment's own Anthropic API key, `/groundline/<deployment_name>/anthropic-api-key`.
- **Each deployment has its own Anthropic API key, kept in Parameter Store:** only the server reads it, in `server/apply`, when it starts and on every upgrade. `make aws-up` stores it with the AWS CLI and `make aws-destroy` deletes it (keeping a lasting deployment's unless `DATA=delete`), and Terraform and the other scripts only check that it exists, so the Anthropic API key never lands in Terraform state or on a developer's screen. Everyone with access to the AWS project can still read it, which is why the docs recommend a key from an Anthropic Console workspace with a spend limit.
- **IMDSv2 with a hop limit of 1:** every metadata request needs a session token, and the reply that carries the token doesn't get past Docker's bridge network, so the containers can't get the role's credentials. The SSM agent and the startup script run on the server itself and still can. Nothing in the containers uses AWS.
- **Standard CPU credits:** T4g instances default to unlimited credits, which bill CPU use above the baseline at about $0.04 per vCPU-hour with no ceiling. Standard credits slow the server down instead, so it costs what the budget expects.
- **An encrypted 20 GB gp3 root disk that's deleted on termination:** it holds the operating system, the checkout and the image, none of which needs keeping. The provider's `default_tags` don't reach the root disk, so it's tagged separately.
- **The 8-hour limit:** the startup script writes a deadline and installs a systemd timer that shuts the server down once it passes. `instance_initiated_shutdown_behavior = "terminate"` makes that shutdown delete the server. The deadline is written before anything else, so a server whose startup fails still goes away. Each upgrade starts the limit again. With `auto_terminate_hours = 0`, as a lasting deployment needs, there's no timer, and a shutdown only stops the server.
- **A budget for the whole project, without credits:** $5 for a disposable deployment and by default $35 for a lasting one (`budget_amount`). Filtering by a tag needs a cost-allocation tag activated in the payer account, which a member account of an AWS Organization can't do or even check, and an inactive tag would make the budget count $0 without saying so. So the budget covers everything in the project, and a lasting deployment is best in a project of its own. Credits are left out because on the Free plan they bring the cost to $0, and the alert would never fire.
- **Names and tags from `deployment_name`:** IAM role and budget names are unique per account, so this is what lets several developers share one AWS project.
- **Their own AWS CLI profile, and a Terraform workspace for each AWS project:** the scripts sign in as `groundline-deploy`, which `make aws-up` and `make aws-project` sign in to the project chosen in the browser, and set `TF_WORKSPACE` to the account ID, so each project's state is separate and a developer can have a server in each at once. No account ID is written into the repository.
- **The server clones the code without signing in:** `main` comes from groundline-dev/groundline, and a branch from whichever GitHub remote has it, such as a fork. `scripts/aws-deploy` passes that remote's HTTPS address as `repository_url`, and checks that it can be cloned without signing in.

## Costs

A deployment costs about $0.025 an hour in us-east-1, about $18 a month: $0.0168 for the `t4g.small` instance, $0.005 for its Elastic IP, $0.002 for its 20 GB root disk and $0.0005 for its 5 GiB data volume. Prices differ a little between Regions. A disposable server that runs to its 8-hour limit costs about $0.20. After `make aws-destroy` keeps a lasting deployment's data volume and Elastic IP, they cost about $4 a month, and a pre-upgrade snapshot a few cents. The security group, IAM role, instance profile and budget are free. `scripts/aws-common.sh` quotes the prices in the summary it shows before `make aws-deploy` changes anything, and `scripts/aws-up` and `scripts/aws-destroy` quote the monthly costs, so update them there too.

## What needs updating

- **Caddy:** review updates to its digest in `compose.https.yaml` and run `make https-smoke` before accepting them. Test fixture Pebble/Python digests live in `tests/https_smoke.py`; update them deliberately with the same lifecycle test.
- **Compose and buildx:** `bootstrap.sh.tftpl` downloads both at pinned versions and checks each file's sha256, since Amazon Linux doesn't package them. To update one, change its URL and its sha256 in the `install_plugin` call, taking the new one from the release's checksums file.
- **Terraform and the AWS provider:** `versions.tf` and `data/versions.tf` pin both with `~>`. Raise the pins, run `terraform -chdir=infra init -upgrade` and `terraform -chdir=infra/data init -upgrade`, then `make infra-check`, and commit both updated `.terraform.lock.hcl` files.
- **The SSM agent's actions:** our policy copies `AmazonSSMManagedInstanceCore` by hand. If AWS adds actions to that policy and Run Command stops working, compare the two.
- **The Anthropic parameter's key:** the role has no `kms:Decrypt`. Reading the parameter works only because it uses the default `aws/ssm` key. Moving it to a customer managed key breaks startup until the role gets `kms:Decrypt` on that key.
- **The AMI:** each new server gets the newest Amazon Linux 2023 arm64 AMI. `ignore_changes = [ami]` keeps a newer AMI from replacing a running server, and upgrades happen in place, so a running server keeps the AMI it started with. Updating a lasting deployment's operating system is ALL-113's; since its data is on its own volume, replacing the root volume is an option there.
- **App settings:** `app_settings` in `variables.tf` lists the settings `compose.yaml` forwards to the app. A test in `tests/test_compose.py` fails until a new one is added to both.

## State

Terraform state stays in this folder and in `data/` (in `terraform.tfstate.d/<account ID>/`, which git ignores). There's no backend and no locking, since each deployment has one owner. As a result, only the clone that created a deployment can update or destroy it; handing a lasting deployment over means copying both state folders and `terraform.tfvars`. `terraform.tfvars` is the same for every AWS project a clone deploys to, so a clone that holds a lasting deployment deploys nothing else (`guard_lasting_folder` in `scripts/aws-common.sh`). Moving state to S3, with a backend block and `terraform init -migrate-state`, is ALL-110, which can lift both limits. `tests/test_infra.py` fails on a backend block, so that test changes too.

## Tests

The tests live in three places:

| Test location | What it tests | Command |
|---|---|---|
| `infra/tests/` | The server's infrastructure, networking, permissions and settings | `make infra-check` |
| `infra/data/tests/` | The data volume and Elastic IP, kept apart from the server | `make infra-check` |
| `tests/` | The app and the deployment scripts, including checks of these files written in Python | `make check` |

`make infra-check` runs `terraform fmt`, `validate` and `test`. The tests plan and apply against a mock AWS provider, so they need Terraform but no AWS credentials, and CI runs them. Each file covers one part:

- `network.tftest.hcl`: public TCP 443, `tester_cidr` validation and access metadata independent of replacement-triggering bootstrap data
- `iam.tftest.hcl`: the role's trust policy, its names, that it can read only the Anthropic parameter, and that only a server with a deadline can release its own Elastic IP
- `instance.tftest.hcl`: IMDSv2 and the hop limit, CPU credits, the disk, the time limit, the `deployment_name` and `lifetime` validation, the startup script, the data volume and Elastic IP, app settings and the outputs
- `budget.tftest.hcl`: the amount for each lifetime, the thresholds and leaving out credits
- `instance.tftest.hcl` also tests `modules/app-settings`, which holds the rules for `app_settings` that both folders apply
- `data/tests/data.tftest.hcl`: the data volume's size, encryption, tags and Availability Zone, the Elastic IP and the outputs

The mock provider doesn't apply `default_tags` and can't list every resource of a kind, so [tests/test_infra.py](../tests/test_infra.py), which `make check` runs, reads the files instead. Among other things, it checks that every resource is tagged, that no Region or backend is written in, that the role gets no managed policy, that the security group has one inbound rule, and that only `server/apply` reads the Anthropic API key. [tests/test_server_apply.py](../tests/test_server_apply.py) runs `server/apply` against fake IMDS, AWS and Docker commands.

`make https-smoke` first checks actual Compose app-first network allocation with production address relationships translated onto a Docker-reserved test subnet of the same size, then runs real Caddy and Pebble in isolated Docker containers: TLS-ALPN IP issuance from a CA outside the application allowlist, NAT and no-SNI selection, trust and identity rejection, allowed and denied callers and forwarded-header spoofing, retained certificates and restrictions on restart, automatic renewal under restriction with a changed served fingerprint, a response after 125 seconds of silence, and expiry and recovery during a CA outage. Only the fixture trusts its test root; it never contacts a public CA. It removes its containers, network and volume on completion. CI runs it separately. This proves the lifecycle, not public trust: `make aws-smoke` must still pass against a fresh EC2 endpoint with ordinary trust stores.

`tests/test_https_bootstrap.py` executes the renderer against fake IMDS responses. `tests/test_aws_access_modes.py` exercises first setup, remembered and missing modes, selection for unrecognized settings and rejected direct deployments, cancellation, keeping an existing server, the data volume and in-place upgrades. `tests/test_aws_lasting.py` covers lasting deployments: the lifetime question, the folder guard, snapshots and teardown that keeps the data. `tests/test_aws_upgrade_smoke.py` covers `make aws-upgrade-smoke`. These tests reach no AWS services.

### Repeatable HTTPS lifecycle checks

Use `make aws-https-smoke` on an existing HTTPS deployment. `RESTART=caddy` adds a confirmed proxy restart; `RESTART=host` additionally reboots EC2. The orchestration lives in `scripts/aws-https-smoke-test`, reuses `aws-smoke-test`, and obtains structured TLS evidence through `scripts/https_check.py --json`. Its bounded SSM path is in `scripts/aws-common.sh`. The child smoke check also runs `https_check.py --source-filter` inside the app container: it connects to Caddy at the configured trusted proxy address while verifying the public IP certificate, spoofs the allowed source in forwarded headers and requires 403 in restricted mode or 200 in unrestricted mode. `tests/test_aws_https_smoke.py` covers target and preflight failures, cancellation, recovery, boot identity, certificate storage and expiry without reaching AWS. See the [operator guide](../docs/deploying-to-aws.md#testing-https-restart-recovery) for interruption and timeout behavior.

The command records the certificate fingerprint and the named certificate volume's identity and creation time. It checks recovery for up to five minutes after restarting Caddy, or ten minutes after requesting an EC2 reboot. SSM probes and individual network calls have their own limits, so a final in-flight probe can finish just after that recovery window. The host check requires a changed boot ID, successful public-IP discovery before Docker startup, unchanged expiry, matching access mode and allowed CIDR and retained certificate storage. Full smoke checks run again after each recovery, with shorter timeouts for readiness checks and requests and without calling the model. Temporary-key revocation failure makes the run fail.

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
   - **Disposable or lasting:** choose disposable. A lasting deployment would keep this folder for itself; test that from a separate worktree, then delete it with `make aws-destroy DATA=delete`.
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

The public-IP endpoint decision is recorded in [ADR 0014](../docs/adr/0014-public-ip-https-endpoints.md); [ADR 0015](../docs/adr/0015-https-only-aws-access-modes.md) records HTTPS-only access and the restricted default, and [ADR 0019](../docs/adr/0019-aws-deployments-upgrade-in-place.md) in-place upgrades with data on its own volume. Restart recovery, boot-time IP discovery and local certificate lifecycle checks validate that HTTPS remains usable across routine restarts and certificate expiry. `make aws-upgrade-smoke` checks that an upgrade keeps the server, its address, its certificate and its API keys.

HTTPS uses a dedicated Docker proxy subnet, `172.30.254.0/29`, with Caddy at `172.30.254.2` and the app at `172.30.254.3`. Both addresses are explicit so app-first startup cannot dynamically allocate Caddy's trusted address to the app. The app trusts forwarded headers only from Caddy. If this subnet conflicts with host routing, change the subnet, both container addresses and trusted address together before deploying. Other app/database traffic remains on the default Compose network.
