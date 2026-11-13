# Deploying to AWS

Test deployments are for groundline's developers to test groundline internally, not for customers or other real callers. A test deployment runs groundline's Compose stack, at a commit you choose, on an EC2 server in your AWS project. Only your computer's public IP can reach it, over plain HTTP, so use only test data with it. Serving real callers requires HTTPS first.

Each test deployment is disposable. Its data survives container restarts and reboots until `make aws-destroy`, a deployment of another commit, or the time limit deletes the server and the data with it.

You can deploy to an AWS project of your own or to one you share with teammates, such as the Groundline project. Several developers can share one project, each with their own test deployment, named and tagged after the `deployment_name` in their `infra/terraform.tfvars`.

To change the Terraform or the scripts, see [infra/README.md](../infra/README.md).

## Before you start

You need:

- **An AWS project.** Use one of your own, or ask the Groundline project's owner to invite you to it in AWS Settings. To create a project of your own, open [AWS Settings](https://settings.aws.com), choose Project, then Create project. `make aws-up` can't create one. The first time, `make aws-up` asks you to sign in to the project at the AWS Management Console and copy its Region from there, then opens your browser so you can choose that project's session.
- **Access in that project to what a deployment creates and checks:**
  - EC2 instances and security groups, in the Region's default VPC
  - an IAM role, its policy and an instance profile, and `iam:PassRole` to give the role to the server
  - SSM Run Command, to run commands on the server, and Parameter Store, for your Anthropic API key
  - an AWS Budgets budget

  AWS manages team members' permissions in AWS Settings, so if a command fails with Access Denied on one of these, ask the project's owner.
- **Terraform 1.17 or later, the AWS CLI v2, `jq`, Git and `make`, on macOS or Linux.** You don't need Docker on your computer: the server installs Docker and Compose and builds the image itself. On macOS with Homebrew, `make aws-up` offers to install Terraform, the AWS CLI and `jq`; otherwise, install them yourself ([step 1](#1-install-the-tools)).
- **An Anthropic API key**, so the server can call Claude. Everyone who has access to the project can read it while your server runs, so for a shared project, use a separate key from an Anthropic Console workspace with a low monthly spend limit ([step 4](#4-store-your-anthropic-api-key)).
- **To deploy your own branch, a public GitHub repository that has it**, such as your fork. The server clones the code without signing in. To deploy `main`, you don't need to push anything.

## Quick start

```sh
make aws-up        # set up what's missing, deploy, and check the server
make aws-destroy   # when you're done
```

On every run, `make aws-up` asks two questions:

- **Which AWS project to deploy to.** It shows the project you're signed in to, such as `Groundline (123456789012), in us-east-2`. Answer `y` to deploy there, or `n` to choose another. Choosing another, or signing in for the first time, asks you to sign in to the project in the AWS Management Console, then for its Region, and opens your browser, where you choose the project's session. When your sign-in has expired, it names the project's account ID and asks whether to sign in again to that project, which only opens your browser.
- **Which code the server runs:** `main` from groundline-dev/groundline, or your current branch.

If your server is already running in that project, it says which commit it runs and asks whether to keep it. Keeping it skips the other questions, then checks the server.

Otherwise, it asks only about what isn't set up yet:

1. It offers to install Terraform, the AWS CLI and `jq` with Homebrew, if any is missing ([step 1](#1-install-the-tools)).
2. It asks for your deployment name and email address ([step 3](#3-name-your-deployment)).
3. It stores your Anthropic API key for this deployment ([step 4](#4-store-your-anthropic-api-key)).
4. For your branch, it offers to push it if no GitHub remote has it yet, and warns you about uncommitted changes, because the server runs your last commit without them.

It then runs `make aws-deploy` and `make aws-smoke`.

## Commands

```sh
make aws-up               # set up what's missing, then deploy and check
make aws-project          # choose the AWS project the other commands act on
make aws-deploy           # create the server, or update it; says what will change and asks first
make aws-smoke            # check it from here, without calling Claude
make aws-smoke-live       # also send one real commission check
make aws-key              # create an API key for trying requests (REVOKE=<ID> to revoke one)
make aws-status           # show when it terminates itself
make aws-reset-deadline   # move that to 8 hours from now (HOURS=n for fewer)
make aws-destroy          # delete it, then check that everything is gone
```

## Setting up by hand

`make aws-up` does these steps for you, except that it can install the tools only where Homebrew is installed.

### 1. Install the tools

Install Terraform 1.17 or later, the AWS CLI v2 and `jq`. With Homebrew:

```sh
brew install hashicorp/tap/terraform awscli jq
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

`make aws-up` stores it for you. It offers `AWS_ANTHROPIC_API_KEY` from your `.env` first, then `ANTHROPIC_API_KEY`, showing each key's last 4 characters before using it, and otherwise asks you to paste one. Set `AWS_ANTHROPIC_API_KEY` to keep a separate key for test deployments next to your everyday one.

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
| `auto_terminate_hours` | `8`; `0` turns the time limit off | `infra/terraform.tfvars` |
| `instance_type` | `t4g.small` | `infra/terraform.tfvars` |
| `REF` | The commit you have checked out | `REF=<branch, tag or commit> make aws-deploy`; `make aws-up` asks instead |
| `REPOSITORY_URL` | The GitHub remote that has the commit; `main` comes from groundline-dev/groundline | `REPOSITORY_URL=<https URL> make aws-deploy` |
| `AWS_ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` from `.env`, or a key you paste | `.env` ([step 4](#4-store-your-anthropic-api-key)) |
| `FULL_PLAN` | Off, so only a short plan is shown | `FULL_PLAN=1 make aws-deploy` |

`infra/terraform.tfvars.example` lists the `terraform.tfvars` settings, and `.env.example` lists `AWS_ANTHROPIC_API_KEY` next to the local settings. `make aws-deploy` fills in the rest of Terraform's variables: the Region, your public IP and the commit.

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

## Checking the server

`make aws-smoke` waits up to 15 minutes for `/health` to answer 200, showing the time so far. It then checks that API keys work, without calling Claude. It creates an API key on the server with `groundline keys create`, through SSM Run Command, and sends `POST /v1/omissions` with an empty body twice:

- With an unknown API key, it expects 401, which shows the server turns away keys it doesn't know.
- With the new API key, it expects 422. The server checks the API key before the body, so 422, for the empty body's missing fields, shows the API key was accepted. Looking the key up needs Postgres and the migrations, so this also shows they work. The body is rejected before any Claude call, so no Anthropic credit is spent.

It revokes that API key at the end, even when a check fails.

`make aws-smoke-live` runs the same checks, then sends the shortest test input to `POST /v1/commissions` and expects 200. It costs under a cent. Run it before demos, and after changing a prompt, a preset, the model or how the Anthropic API key reaches the server.

Both smoke tests check only that the server is set up correctly and, for `make aws-smoke-live`, that it can reach Claude. A passing run doesn't show that a commit is ready to release.

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

To try your summary app against a test deployment, point the app at the server instead of a local groundline:

- **Base URL:** the server's address, such as `http://203.0.113.10:8001`. `make aws-up` prints it under What next, and `make aws-status` shows it too. The app calls `POST <base URL>/v1/omissions` and `POST <base URL>/v1/commissions`, with the same requests and responses as a local server ([README](../README.md#post-v1omissions)). Set the app's HTTP client timeout above the configured whole-check limit, which defaults to 120 seconds, to allow time for request and response transfer.
- **API key:** one from `make aws-key`, sent as `Authorization: Bearer <API key>`. It's a groundline API key, separate from the Anthropic API key the server uses.
- **Settings, not code:** a new server gets a new address, and its API keys go with the old one. Have the app read both from settings, such as `GROUNDLINE_URL` and `GROUNDLINE_API_KEY`, so switching servers doesn't need a code change.

Only your computer can reach the server: its firewall accepts requests only from the public IP address you deployed from. So run the app on the same computer, for example in local development; a hosted copy of the app or a teammate's computer can't reach it. Call groundline from the app's backend, since groundline doesn't send CORS headers, so JavaScript in a browser page on another origin can't call it directly.

The server uses plain HTTP, without TLS, and a test deployment is for internal testing only. Send it test data, never real patient notes.

## Costs

The server costs about $0.024 an hour, so one that runs to its 8-hour limit costs about $0.19, and one with no limit about $17 a month. It slows down instead of running up extra charges once its CPU credits run out. The budget alert emails everyone who has deployed when the project's costs pass $2.50 and $5 in a month. [infra/README.md](../infra/README.md#costs) breaks the cost down.

## Troubleshooting

- **`make aws-smoke` times out:** it prints the end of the server's startup log (`/var/log/groundline-bootstrap.log`) and the containers' status and logs. A failed startup also leaves `/var/lib/groundline/bootstrap-failed`.
- **Requests time out from a new network:** your IP changed. Rerun `make aws-deploy` with the same commit: only the security group changes.
- **`make aws-deploy` fails because a role or budget already exists:** someone else in the AWS project uses the same `deployment_name`. Pick another one in `infra/terraform.tfvars`.
- **AWS calls that worked start failing with Access Denied:** your project may have reached its spend limit, which pauses it. Check in AWS Settings, under Billing.

## Tearing down

`make aws-destroy` says in plain words what it will delete, such as your server and its data, and asks before deleting anything. It then asks AWS whether the instance, security group, IAM role, instance profile and budget are gone, and fails if any is still there or AWS can't say.

It also deletes your Anthropic API key from Parameter Store. If anyone else can access the AWS project, they could have copied the key while your server ran, so revoke it in the Anthropic Console if that matters to you.

Terraform state stays in `infra/` on your computer. Delete the server with `make aws-destroy` from the same folder you created it from; another clone doesn't know about it.
