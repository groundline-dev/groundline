# Deploying to AWS

A test deployment runs groundline's Compose stack, at a commit you choose, on an EC2 server in your AWS project. Only your computer's public IP can reach it, over plain HTTP, so it's for testing with test data. Real callers need HTTPS first (ALL-90).

Each test deployment is disposable. Its data survives container restarts and reboots until `make aws-destroy`, a deployment of another commit, or the time limit deletes the server and the data with it. Several developers can share one AWS project, each with their own test deployment, named and tagged after the `deployment_name` in their `infra/terraform.tfvars`.

To change the Terraform or the scripts, see [infra/README.md](../infra/README.md).

## Quick start

```sh
make aws-up        # set up what's missing, deploy, and check the server
make aws-destroy   # when you're done
```

`make aws-up` asks only about what isn't set up yet:

1. It offers to install Terraform, the AWS CLI and `jq` with Homebrew, if any is missing ([step 1](#1-install-the-tools)).
2. It asks for your Region and signs you in ([step 2](#2-sign-in-and-set-your-region)).
3. It asks for your deployment name and email address ([step 3](#3-name-your-deployment)).
4. It offers to store the Anthropic API key ([step 4](#4-store-the-anthropic-api-key)).
5. It offers to push your branch, since the server clones your commit from GitHub. It warns you about uncommitted changes, because the server runs your last commit without them.

It then runs `make aws-deploy` and `make aws-smoke`. Rerunning it skips the setup.

## Commands

```sh
make aws-up           # set up what's missing, then deploy and check
make aws-deploy       # create the server, or update it; says what will change and asks first
make aws-smoke        # check it from here, without calling Claude
make aws-smoke-live   # also send one real commission check
make aws-key          # create an API key for trying requests (REVOKE=<ID> to revoke one)
make aws-status       # show when it terminates itself
make aws-extend       # move that to 8 hours from now (HOURS=n for fewer)
make aws-destroy      # delete it, then check that everything is gone
```

## Setting up by hand

`make aws-up` does these steps for you, except that it can install the tools only where Homebrew is installed.

### 1. Install the tools

Install Terraform 1.17 or later, the AWS CLI v2 and `jq`. With Homebrew:

```sh
brew install hashicorp/tap/terraform awscli jq
```

Otherwise, follow [Terraform's install guide](https://developer.hashicorp.com/terraform/install) and [the AWS CLI's](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), and install `jq` with your package manager.

The server goes in your Region's default VPC, which every new AWS project has. If you deleted yours, `aws ec2 create-default-vpc` creates it again.

### 2. Sign in and set your Region

Sign in with `aws login`. Find your project's Region in AWS Settings, under View all projects > Overview > Additional Info > Region, and set it by its code, such as `us-east-2`, not its name, such as US East (Ohio):

```sh
aws configure set region us-east-2   # your project's Region code
```

The scripts use `AWS_REGION` instead if it's set.

### 3. Name your deployment

Copy the settings file and fill in your `deployment_name`, such as your first name, and your email address:

```sh
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

The deployment name has to be different from everyone else's in the AWS project. The $5-a-month budget alert goes to the email address. It covers your whole AWS project, not only your server, so everyone sharing the project gets it when anyone's server runs long.

### 4. Store the Anthropic API key

To make model calls, store an Anthropic API key in Parameter Store as `/groundline/anthropic-api-key`. Without it, the server still starts and `make aws-smoke` still passes, but every model call fails with 503. `read -rs` keeps the Anthropic API key off the screen and out of your shell history, and piping it to `--value file:///dev/stdin` keeps it out of the command's arguments, which other processes can read:

```sh
read -rs ANTHROPIC_KEY   # paste the key and press Enter
printf '%s' "$ANTHROPIC_KEY" | aws ssm put-parameter --name /groundline/anthropic-api-key \
  --type SecureString --value file:///dev/stdin
unset ANTHROPIC_KEY
```

Every server in the AWS project reads this one parameter, so only the first person to deploy needs to create it. To change it, add `--overwrite` to `put-parameter`, then run `make aws-destroy` and `make aws-deploy`.

## Deploying

`make aws-deploy` runs the commit you have checked out, which must be pushed to GitHub, since the server clones it from there. To run another, use `REF=<branch, tag or commit> make aws-deploy`. Before changing anything, it says in plain words what will change, such as replacing your server and deleting its data, and asks you to type `yes`.

The server takes about 5 to 10 minutes to build the image and start. Deploying another commit replaces the server, with its data.

## The time limit

Each server shuts itself down 8 hours after it first starts, and AWS then terminates it, deleting its disk and its data. `make aws-deploy` prints the time, and `make aws-status` shows how long is left. A redeploy of the same commit doesn't reset the limit, but a redeploy of another commit does, since that creates a new server.

`make aws-extend` moves the deadline to 8 hours from now, or to `HOURS=n` hours from now for fewer. It never moves it earlier, and never more than 8 hours ahead, so a server outlasts the limit only while someone keeps extending it. To change the limit, set `auto_terminate_hours` in `infra/terraform.tfvars` and redeploy; 0 turns it off.

Terminating removes only the server and its data. Its security group, IAM role, instance profile and budget stay, and a later deployment with the same `deployment_name` fails until they're gone. `make aws-status` and `make aws-smoke` tell you when the server has terminated: run `make aws-destroy` to remove what's left, or `make aws-deploy` for a new server.

## Checking the server

`make aws-smoke` waits up to 15 minutes for `/health` to answer 200, showing the time so far. It then checks that API keys work, without calling Claude. It creates an API key on the server with `groundline keys create`, through SSM Run Command, and sends `POST /v1/omissions` with an empty body twice:

- With an unknown API key, it expects 401, which shows the server turns away keys it doesn't know.
- With the new API key, it expects 422. The server checks the API key before the body, so 422, for the empty body's missing fields, shows the API key was accepted. Looking the key up needs Postgres and the migrations, so this also shows they work. The body is rejected before any Claude call, so no Anthropic credit is spent.

It revokes that API key at the end, even when a check fails.

`make aws-smoke-live` runs the same checks, then sends the shortest test input to `POST /v1/commissions` and expects 200. It costs under a cent. Run it before demos and releases, and after changing a prompt, a preset, the model or how the Anthropic API key reaches the server.

## API keys for a demo

The smoke test revokes its own API key. To create one that lasts, run `make aws-key`. It runs `groundline keys create` on the server through SSM Run Command and prints the API key and its ID. The `/docs` page itself doesn't need an API key, but sending a request from it does: choose Authorize and paste the API key.

SSM keeps each command's output, so anyone who can read your project's Run Command history can see the API key. It goes when the server does; to revoke it sooner, run `make aws-key REVOKE=<ID>`.

## Costs

The server costs about $0.024 an hour, so one that runs to its 8-hour limit costs about $0.19, and one with no limit about $17 a month. It slows down instead of running up extra charges once its CPU credits run out. The budget alert emails everyone who has deployed when the project's costs pass $2.50 and $5 in a month. [infra/README.md](../infra/README.md#costs) breaks the cost down.

## Troubleshooting

- **`make aws-smoke` times out:** it prints the end of the server's startup log (`/var/log/groundline-bootstrap.log`) and the containers' status and logs. A failed startup also leaves `/var/lib/groundline/bootstrap-failed`.
- **Requests time out from a new network:** your IP changed. Rerun `make aws-deploy` with the same commit: only the security group changes.
- **`make aws-deploy` fails because a role or budget already exists:** someone else in the AWS project uses the same `deployment_name`. Pick another one in `infra/terraform.tfvars`.
- **AWS calls that worked start failing with Access Denied:** your project may have reached its spend limit, which pauses it. Check in AWS Settings, under Billing.

## Tearing down

`make aws-destroy` says in plain words what it will delete, such as your server and its data, and asks before deleting anything. It then asks AWS whether the instance, security group, IAM role, instance profile and budget are gone, and fails if any is still there or AWS can't say.

It leaves the Anthropic API key's parameter, which Terraform didn't create, and prints the command to delete it. Everyone in the AWS project uses that parameter, so delete it only if no one else still deploys.

Terraform state stays in `infra/` on your computer. Delete the server with `make aws-destroy` from the same folder you created it from; another clone doesn't know about it.
