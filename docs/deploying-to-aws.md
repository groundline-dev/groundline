# Deploying to AWS

A disposable deployment runs groundline's Compose stack, at a commit you choose, on one EC2 server in your AWS project. Both access modes serve `https://<public-IPv4>`, with automatic publicly trusted certificates and no domain or DNS setup:

- **`restricted` (default):** a test deployment whose application routes are accessible only from the public IPv4 address you deploy from, including health probes and API documentation.
- **`unrestricted`:** a disposable deployment whose application routes accept callers from any IP. Health probes and API documentation need no API key.

Protected API endpoints require groundline API keys in both modes. HTTP deployment is no longer supported. In restricted mode, Caddy rejects application requests from other source IPs with 403. TCP/TLS connections on 443 remain public so the certificate authority can validate and renew the certificate; the restriction applies to application requests, not TLS connectivity. Computers sharing the allowed public IP also share this network access.

Both modes are disposable and for synthetic test data only ([ADR 0006](../docs/adr/0006-no-real-patient-data-before-production.md)). HTTPS does not make this a production or lasting deployment. Data and API keys survive container restarts and reboots, but `make aws-destroy`, instance replacement, or the default eight-hour expiry deletes them.

You can deploy to an AWS project of your own or to one you share with teammates, such as the groundline project. Several developers can share one project, each with their own disposable deployment, named and tagged after the `deployment_name` in their `infra/terraform.tfvars`.

To change the Terraform or the scripts, see [infra/README.md](../infra/README.md).

## Before you start

You need:

- **An AWS project.** Use one of your own, or ask the groundline project's owner to invite you to it in AWS Settings. To create a project of your own, open [AWS Settings](https://settings.aws.com), choose Project, then Create project. `make aws-up` can't create one. The first time, `make aws-up` asks you to sign in to the project at the AWS Management Console and copy its Region from there, then opens your browser so you can choose that project's session.
- **Access in that project to what a deployment creates and checks:**
  - EC2 instances and security groups, in the Region's default VPC
  - an IAM role, its policy and an instance profile, and `iam:PassRole` to give the role to the server
  - SSM Run Command, to run commands on the server, and Parameter Store, for your Anthropic API key
  - an AWS Budgets budget

  AWS manages team members' permissions in AWS Settings, so if a command fails with Access Denied on one of these, ask the project's owner.
- **Terraform 1.17 or later, the AWS CLI v2, `jq`, `uv`, Git and `make`, on macOS or Linux.** You don't need Docker on your computer: the server installs Docker and Compose and builds the image itself. On macOS with Homebrew, `make aws-up` offers to install Terraform, the AWS CLI, `jq` and `uv`; otherwise, install them yourself ([step 1](#1-install-the-tools)).
- **An Anthropic API key**, so the server can call Claude. Everyone who has access to the project can read it while your server runs, so for a shared project, use a separate key from an Anthropic Console workspace with a low monthly spend limit ([step 4](#4-store-your-anthropic-api-key)).
- **To deploy your own branch, a public GitHub repository that has it**, such as your fork. The server clones the code without signing in. To deploy `main`, you don't need to push anything.

## Quick start

```sh
make aws-up        # set up what's missing, deploy, and check the server
make aws-destroy   # when you're done
```

On every run, `make aws-up` asks two questions:

- **Which AWS project to deploy to.** It shows the project you're signed in to, such as `groundline (123456789012), in us-east-2`. Answer `y` to deploy there, or `n` to choose another. Choosing another, or signing in for the first time, asks you to sign in to the project in the AWS Management Console, then for its Region, and opens your browser, where you choose the project's session. When your sign-in has expired, it names the project's account ID and asks whether to sign in again to that project, which only opens your browser.
- **Which code the server runs:** `main` from groundline-dev/groundline, or your current branch.

If your server is already running in that project, it says which commit it runs and asks whether to keep it. The option to keep and check the server is available only when the deployed access mode matches the saved mode. If they differ, it explains the difference and offers a deployment plan or cancellation. Terraform describes replacement and loss of the database and API keys before asking to proceed. For a restricted server, a changed public IP stops before the keep prompt and provides an update command targeting the deployed commit; unrestricted keep does not discover a tester IP.

Otherwise, it asks only about what isn't set up yet:

1. It offers to install Terraform, the AWS CLI, `jq` and `uv` with Homebrew, if any is missing ([step 1](#1-install-the-tools)).
2. It asks for your deployment name, email address and access mode (IP-restricted HTTPS by default) ([step 3](#3-name-your-deployment)).
3. It stores your Anthropic API key for this deployment ([step 4](#4-store-your-anthropic-api-key)).
4. For your branch, it offers to push it if no GitHub remote has it yet, and warns you about uncommitted changes, because the server runs your last commit without them.

It then runs `make aws-deploy` and `make aws-smoke`.

## Commands

```sh
make aws-up               # set up what's missing, then deploy and check
make aws-project          # choose the AWS project the other commands act on
make aws-deploy           # create the server, or update it; says what will change and asks first
make aws-smoke            # check it from here, without calling the model
make aws-smoke-live       # also send one real commission check
make aws-key              # create an API key for trying requests (REVOKE=<ID> to revoke one)
make aws-status           # show when it terminates itself
make aws-reset-deadline   # move that to 8 hours from now (HOURS=n for fewer)
make aws-destroy          # delete it, then check that everything is gone
```

## Setting up by hand

`make aws-up` does these steps for you, except that it can install the tools only where Homebrew is installed.

### 1. Install the tools

Install Terraform 1.17 or later, the AWS CLI v2, `jq` and `uv`. With Homebrew:

```sh
brew install hashicorp/tap/terraform awscli jq uv
```

Otherwise, follow [Terraform's install guide](https://developer.hashicorp.com/terraform/install) and [the AWS CLI's](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), and install `jq` with your package manager. You also need Git and `make`, which macOS installs with its command line tools (`xcode-select --install`). You don't need Docker: the server installs Docker and Compose and builds the image.

The server goes in your Region's default VPC, which every new AWS project has. If you deleted yours, `aws ec2 create-default-vpc` creates it again.

### 2. Sign in and set your Region

The scripts sign in with their own AWS CLI profile, `groundline-deploy`, so they don't change your other profiles. `make aws-up` and `make aws-project` set it up for you. To find the project's Region, open the AWS Management Console, go to Manage projects > Overview > Additional Info > Region, and use its Copy button. Set the profile's Region to that code, such as `us-east-2`. Then sign in, and choose that project's session in the browser:

```sh
aws configure set region us-east-2 --profile groundline-deploy   # the project's Region code
aws login --profile groundline-deploy
```

To use one of your own profiles instead, set `AWS_PROFILE`. The scripts use `AWS_REGION` instead of the profile's Region if it's set.

### 3. Name your deployment

Copy the settings file and fill in your `deployment_name`, such as your first name, and your email address:

```sh
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

The deployment name has to be different from everyone else's in the AWS projects you deploy to. The $5-a-month budget alert goes to the email address. It covers your whole AWS project, not only your server, so everyone sharing the project gets it when anyone's server runs long.

### 4. Store your Anthropic API key

To make model calls, your server needs your Anthropic API key in Parameter Store, as `/groundline/<deployment_name>/anthropic-api-key`. Its IAM role can read only that parameter. Without it, the server still starts and `make aws-smoke` still passes, but every model call fails with 503. `make aws-destroy` deletes the parameter, so each new server needs it stored again.

`make aws-up` stores it for you. It offers `AWS_ANTHROPIC_API_KEY` from your `.env` first, then `ANTHROPIC_API_KEY`, showing each key's last 4 characters before using it, and otherwise asks you to paste one. Set `AWS_ANTHROPIC_API_KEY` to keep a separate key for disposable deployments next to your everyday one.

When a key is already stored, `make aws-up` asks whether to keep it. If you answer no, it asks for a new key and overwrites the stored one. The server reads the key only when it starts, so a running server keeps the old key until it's replaced: deploy a different commit, or run `make aws-destroy`, then `make aws-up`.

To store it by hand, `read -rs` keeps the Anthropic API key off the screen and out of your shell history, and piping it to `--value file:///dev/stdin` keeps it out of the command's arguments, which other processes can read:

```sh
read -rs ANTHROPIC_KEY   # paste the key and press Enter
printf '%s' "$ANTHROPIC_KEY" | aws ssm put-parameter --profile groundline-deploy --region us-east-2 \
  --name /groundline/alice/anthropic-api-key --type SecureString --value file:///dev/stdin
# your profile, the project's Region and your deployment_name in place of groundline-deploy, us-east-2 and alice
unset ANTHROPIC_KEY
```

To change it, add `--overwrite` to `put-parameter`, then run `make aws-destroy` and `make aws-up`.

Everyone who has access to the AWS project can read the key, both in Parameter Store and on the server while it runs. To keep your Anthropic API key private, deploy to a project that only you own.

If you deploy to a shared project, use a separate Anthropic API key, created in an Anthropic Console workspace with a low monthly spend limit. Anyone who copies that key can spend no more than the limit, and you can revoke it without touching your everyday key.

## Settings

| Setting | Default | Where to set it |
| --- | --- | --- |
| AWS project | The one the scripts' profile is signed in to | `make aws-up` or `make aws-project`, which open your browser to choose it |
| AWS CLI profile | `groundline-deploy` | `AWS_PROFILE`, to use one of your own profiles |
| Region | The Region saved in the profile | `make aws-up` or `make aws-project` ask for it; `AWS_REGION` overrides it |
| `deployment_name` | None; required | `infra/terraform.tfvars`, which `make aws-up` fills in |
| `budget_alert_email` | None; required | `infra/terraform.tfvars`, which `make aws-up` fills in |
| `access_mode` | `restricted` | One literal `access_mode = "restricted"` or `"unrestricted"` in `infra/terraform.tfvars` |
| `auto_terminate_hours` | `8`; `0` turns the time limit off | `infra/terraform.tfvars` |
| `instance_type` | `t4g.small` | `infra/terraform.tfvars` |
| `REF` | The commit you have checked out | `REF=<branch, tag or commit> make aws-deploy`; `make aws-up` asks instead |
| `REPOSITORY_URL` | The GitHub remote that has the commit; `main` comes from groundline-dev/groundline | `REPOSITORY_URL=<https URL> make aws-deploy` |
| `AWS_ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` from `.env`, or a key you paste | `.env` ([step 4](#4-store-your-anthropic-api-key)) |
| `FULL_PLAN` | Off, so only a short plan is shown | `FULL_PLAN=1 make aws-deploy` |

`infra/terraform.tfvars.example` lists the `terraform.tfvars` settings, and `.env.example` lists `AWS_ANTHROPIC_API_KEY` next to the local settings. `make aws-deploy` fills in the rest of Terraform's variables: the Region, the selected mode, the tester IP only for restricted mode, and the commit. Missing settings are saved as restricted HTTPS. For an unrecognized saved value, `make aws-up` presents the two supported modes and saves your selection; cancellation or EOF preserves the setting. Direct `make aws-deploy` stops with instructions to choose through the wizard or correct the file. Deployment scripts pass the supported mode explicitly, so environment or auto-tfvars overrides cannot silently broaden access. This corrects local settings; it does not change a running server until deployment changes are approved and applied. Calling Terraform directly requires valid input values.

### Server size

The default server is a `t4g.small`: 2 vCPUs and 2 GiB of memory, with an encrypted 20 GB gp3 disk. It's the smallest size we've tested; 1 GiB is too little to build the image and run Postgres. For a bigger one, set `instance_type` in `infra/terraform.tfvars`, such as `t4g.medium`. It has to be a Graviton (arm64) type, with a `g` after the generation number, because the server's Amazon Linux image and its startup script are arm64.

### Passwords

There are no database passwords to set. When a new server first starts, it generates both Postgres passwords and writes them to its own `.env`, readable only by root, along with your Anthropic API key from Parameter Store.

## Deploying

The server clones the code from GitHub without signing in. `main` comes from groundline-dev/groundline, so deploying it needs no push and no fork. A branch comes from whichever GitHub remote has it, such as your fork, which has to be public. You don't need permission to push to groundline-dev/groundline.

`make aws-deploy` runs the commit you have checked out. To run another, use `REF=<branch, tag or commit> make aws-deploy`. Before changing anything, it says in plain words what will change, such as replacing your server and deleting its data, and asks you to choose 1 to go ahead. Choosing 2 first shows a short version of Terraform's plan: which resources change, which settings change and what forces a replacement. To see every setting of every resource instead, run it with `FULL_PLAN=1`, such as `FULL_PLAN=1 make aws-up`.

The server takes about 5 to 10 minutes to build the image and start. Deploying another commit replaces the server, with its data.

## Deploying to more than one project

Terraform keeps a separate state for each AWS project, in `infra/terraform.tfstate.d/<account ID>/`, so you can have a server in each project at once. Every `make aws-*` command acts on the project you're signed in to. `make aws-status` and `make aws-destroy` also tell you when another project still has a deployment of yours. To reach it, switch projects first:

```sh
make aws-project   # opens your browser; choose the other project's session
make aws-status    # or make aws-destroy
```

## The time limit

Each server shuts itself down 8 hours after it first starts, and AWS then terminates it, deleting its disk and its data. `make aws-deploy` prints the time, and `make aws-status` shows how long is left. A redeploy of the same commit doesn't reset the limit, but a redeploy of another commit does, since that creates a new server.

`make aws-reset-deadline` moves the deadline to 8 hours from now, or to `HOURS=n` hours from now for fewer. It never moves it earlier, and never more than 8 hours ahead, so a server outlasts the limit only while someone keeps resetting its deadline. To change the limit, set `auto_terminate_hours` in `infra/terraform.tfvars` and redeploy; 0 turns it off.

Terminating removes only the server and its data. Its security group, IAM role, instance profile and budget stay, and a later deployment with the same `deployment_name` fails until they're gone. `make aws-status` and `make aws-smoke` tell you when the server has terminated: run `make aws-destroy` to remove what's left, or `make aws-deploy` for a new server.

## Selecting and switching HTTPS access

1. Set `access_mode = "restricted"` or `"unrestricted"` in `infra/terraform.tfvars`. The wizard uses restricted HTTPS by default. Missing values default to `restricted`; unrecognized values require a choice in the wizard. Choose unrestricted explicitly if callers use other public IPs.
2. Run `make aws-up` and confirm the AWS project and a commit supporting both HTTPS access modes.
3. If a running deployment has another access mode, the wizard offers a deployment plan or cancellation instead of keeping it unchanged.
4. Review the plan. **Switching modes replaces the server and deletes its database and API keys.** Approve only when that loss is acceptable.
5. Wait for deployment and smoke checks to pass, then use the printed HTTPS URL. If the server was replaced, run `make aws-key` and update the calling application's URL and API key.

HTTPS ingress opens during Terraform apply, before the smoke checks run. Wait for those checks to pass before giving callers the endpoint and API keys; the checks do not gate network access.

For a new tester IP, rerun `make aws-deploy` with the same deployed commit and mode. It updates the allowed-IP tag and reloads Caddy through SSM while preserving the server, database, API keys, certificate volumes and original expiry. It retries metadata reconciliation within a bounded window and reports failure if the approved access configuration cannot be loaded. Do not consider that deployment successful until a retry and smoke checks pass.

## HTTPS and certificate lifecycle

Caddy terminates TLS on the existing EC2 instance and talks HTTP to groundline over the same host's private Docker network. Only TCP 443 is open for application requests. The HTTPS Compose override removes the app and PostgreSQL host-port publications. The private app trusts Caddy's forwarded scheme so application redirects stay on HTTPS; Caddy replaces caller-supplied forwarded headers. Port 80, SSH, the Caddy admin API, and direct app/database ports remain closed from the internet.

Caddy uses an explicit Let's Encrypt ACME issuer and the `shortlived` certificate profile. IP certificates last about six days; Caddy obtains, renews and loads replacements automatically. Validation uses TLS-ALPN-01 on 443, so that port must be reachable by the CA. HTTP validation, redirects, and internal-CA fallback are disabled. No Certbot job or manual certificate installation is needed. Clients must use normal certificate verification; do not bypass errors with `-k` or install a private root.

Certificate/account state lives in a separate Docker volume and survives container restarts. The EC2 host reads its public IP through IMDSv2 and renders Caddy's configuration before Docker starts on each boot. The helper also reads the access-mode and allowed-CIDR tags through IMDSv2 and renders the source-IP filter. Missing or invalid metadata blocks Docker startup rather than broadening access. Caddy's default TLS server name handles clients that omit SNI for IP addresses. No application secrets or Docker socket are mounted into Caddy.

An EC2 stop/start or replacement can change the IP. Host startup updates the certificate target, but callers must update their base URL. To refresh stale Terraform outputs after a stop/start, rerun `make aws-deploy` with the same deployed commit and settings, review the plan and then run `make aws-status`. An Elastic IP can later supply the same address input; disposable deployments do not allocate one or retain disks when replaced or removed. These deployments do not configure custom domains.

First issuance and failed renewals depend on CA availability, network access and rate limits. Leave the Caddy volumes intact when retrying: deleting them can create unnecessary new accounts and orders. Certificate failures never expose an HTTP fallback. Monitor `docker compose logs caddy` through SSM; a running container alone does not prove trusted HTTPS readiness. External certificate-expiry alerts are not provided.

## Checking the server

`make aws-smoke` waits up to 15 minutes for `/health` to answer 200, showing the time so far. It validates the public certificate chain and exact IP identity without SNI, and probes ports 80, 8000, 8001, 5432, 5433 and 2019 to ensure they are closed or filtered.

It verifies HTTPS redirects and the configured source-IP policy, including attempts to spoof forwarded headers. A probe from the private app container must receive 403 in restricted mode or 200 in unrestricted mode, using ordinary certificate verification. See the [infrastructure guide](../infra/README.md#repeatable-https-lifecycle-checks) for the probe details.

It then checks API keys without calling the model. It creates an API key on the server with `groundline keys create`, through SSM Run Command, and sends `POST /v1/omissions` with an empty body twice:

- With an unknown API key, it expects 401, which shows the server turns away keys it doesn't know.
- With the new API key, it expects 422. The server checks the API key before the body, so 422, for the empty body's missing fields, shows the API key was accepted. Looking the key up needs Postgres and the migrations, so this also shows they work. The body is rejected before any Claude call, so no Anthropic credit is spent.

It revokes that API key at the end, even when a check fails.

`make aws-smoke-live` runs the same checks, then sends the shortest test input to `POST /v1/commissions` and expects 200. It costs under a cent. Run it before demos, and after changing a prompt, a preset, the model or how the Anthropic API key reaches the server.

Both smoke tests check only that the server is set up correctly and, for `make aws-smoke-live`, that it can reach Claude. A passing run doesn't show that a commit is ready to release.

## Testing HTTPS restart recovery

`make aws-https-smoke` checks an **existing HTTPS deployment** using the same public trust, IP identity, no-SNI, port isolation, redirect and API-key checks as `make aws-smoke`. It first verifies that the selected account and Region, Terraform instance/IP and AWS instance/IP agree, and that SSM and Caddy metadata are accessible and the rendered access mode/allowed CIDR match the deployed settings. It does not create or replace a server.

```sh
make aws-https-smoke                 # no services restarted
make aws-https-smoke RESTART=caddy   # also restart Caddy and verify recovery
make aws-https-smoke RESTART=host    # Caddy check, then EC2 reboot and recovery
```

Restart modes interrupt callers and require typing an explicit choice after the baseline checks; Enter or unattended EOF does not approve them. Use them on a disposable deployment or during an appropriate interruption window. Caddy checks require at least 15 minutes before expiry; host checks require at least 25 minutes. The command rechecks remaining time and target identity immediately before each interruption. It never extends the deadline; `make aws-reset-deadline` remains an explicit separate action.

Recovery waits up to five minutes for Caddy and ten minutes for the host. It verifies retained certificate storage and reruns the smoke checks. See the [infrastructure guide](../infra/README.md#repeatable-https-lifecycle-checks) for the metadata and timing checks.

A matching fingerprint proves the same certificate was served after recovery. A different, valid certificate with retained storage is reported separately: this does not prove renewal or establish why the certificate changed. `make https-smoke` remains the automated accelerated renewal test, using a local ACME server. This AWS command never deletes certificates, forces production issuance, stops/starts EC2 to change its address, or tests days-long production renewal.

On failure, the command stops before any further restart, reports the target, and attempts bounded Docker/service diagnostics. It leaves the deployment running, with its original expiry, and tells you how to inspect or remove it. It never destroys a deployment automatically. Live AWS execution stays outside CI; fake AWS/SSM regression tests run in `make check`.

## Checking the server by hand

To send the requests yourself, get the server's address from Terraform. Terraform keeps each AWS project's state in a workspace named after its account ID, so select that workspace the way the scripts do:

```sh
export AWS_PROFILE=groundline-deploy
export TF_WORKSPACE="$(aws sts get-caller-identity --query Account --output text)"
APP_URL="$(terraform -chdir=infra output -raw app_url)"
curl "$APP_URL/health"   # {"status":"ok"}
```

Then create an API key with `make aws-key`, and send a commission check with it. This calls Claude, for under a cent:

```sh
read -rs API_KEY   # paste the API key from make aws-key and press Enter
curl "$APP_URL/v1/commissions" \
  --header "Authorization: Bearer $API_KEY" \
  --json '{"source": "[doctor] take motrin 600 mg every 6 hours for a week .", "summary": "PLAN: Motrin 800 mg every 6 hours for a week."}'
unset API_KEY
```

It answers 200 with the claims in the summary that the source doesn't support ([README](../README.md#post-v1commissions) describes the response). When you're done, revoke the API key with `make aws-key REVOKE=<ID>`, using the ID `make aws-key` printed.

## API keys for a demo

The smoke test revokes its own API key. To create one that lasts, run `make aws-key`. It runs `groundline keys create` on the server through SSM Run Command and prints the API key and its ID. The `/docs` page itself doesn't need an API key, but sending a request from it does: choose Authorize and paste the API key.

SSM keeps each command's output, so anyone who can read your project's Run Command history can see the API key. It goes when the server does; to revoke it sooner, run `make aws-key REVOKE=<ID>`.

## Calling it from your app

To try your summary app against a disposable deployment, point its backend at the server instead of a local groundline:

- **Base URL:** the server's address, such as `https://203.0.113.10`. `make aws-up` prints it under What next, and `make aws-status` shows it too. The app calls `POST <base URL>/v1/omissions` and `POST <base URL>/v1/commissions`, with the same requests and responses as a local server ([README](../README.md#post-v1omissions)). A check may take longer than two minutes; choose an appropriate client timeout. Caddy adds no response timeout or automatic request retries.
- **API key:** one from `make aws-key`, sent as `Authorization: Bearer <API key>`. It's a groundline API key, separate from the Anthropic API key the server uses.
- **Settings, not code:** a new server gets a new address, and its API keys go with the old one. Have the app read both from settings, such as `GROUNDLINE_URL` and `GROUNDLINE_API_KEY`, so switching servers doesn't need a code change.

Unrestricted mode accepts backend calls from other public IPs. Restricted mode requires the backend to use the configured tester public IP. Keep the API key on that backend. groundline does not send CORS headers; cross-origin browser calls are not supported.

Both modes still prohibit real patient notes.

## Costs

The server costs about $0.024 an hour, so one that runs to its 8-hour limit costs about $0.19, and one with no limit about $17 a month. It slows down instead of running up extra charges once its CPU credits run out. The budget alert emails everyone who has deployed when the project's costs pass $2.50 and $5 in a month. [infra/README.md](../infra/README.md#costs) breaks the cost down.

## Troubleshooting

- **`make aws-smoke` times out:** it prints the end of the server's startup log (`/var/log/groundline-bootstrap.log`) and the containers' status and logs. A failed startup also leaves `/var/lib/groundline/bootstrap-failed`.
- **Restricted HTTPS returns 403 from a new network:** smoke checks report the access denial immediately. Your public IP may have changed. Rerun `make aws-deploy` with the same deployed commit and mode, review the allowed-IP update, then run `make aws-smoke`. The server and data are preserved.
- **`make aws-deploy` fails because a role or budget already exists:** someone else in the AWS project uses the same `deployment_name`. Pick another one in `infra/terraform.tfvars`.
- **AWS calls that worked start failing with Access Denied:** your project may have reached its spend limit, which pauses it. Check in AWS Settings, under Billing.

- **HTTPS certificate errors:** check that the URL uses the current public IPv4, TCP 443 is open, outbound ACME access works, and the host clock is correct. Smoke-test failure output preserves curl's TLS error and Caddy's logs. Do not disable verification or open the app port to work around issuance failure.
- **Docker fails after a reboot:** inspect `journalctl -u docker` through SSM and the `groundline-https` ExecStartPre step. Missing or invalid public-IP/access metadata blocks Docker deliberately. Fix address discovery, then restart Docker; keep the Caddy data volume.

## Tearing down

`make aws-destroy` says in plain words what it will delete, such as your server and its data, and asks before deleting anything. It then asks AWS whether the instance, security group, IAM role, instance profile and budget are gone, and fails if any is still there or AWS can't say.

It also deletes your Anthropic API key from Parameter Store. If anyone else can access the AWS project, they could have copied the key while your server ran, so revoke it in the Anthropic Console if that matters to you.

Terraform state stays in `infra/` on your computer. Delete the server with `make aws-destroy` from the same folder you created it from; another clone doesn't know about it.
