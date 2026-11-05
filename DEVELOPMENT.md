# Developing groundline

How to work on groundline itself. For what it does and how to use it, start with the [README](README.md).

## Getting started

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 (0.13.7 or later) and [Docker](https://docs.docker.com/get-started/get-docker/), installed and running.

1. Install the dependencies and enable the shared git hooks:

   ```sh
   make setup
   ```

2. Create your `.env` from the example. Skip this if you already have one: it replaces the file, and Postgres keeps the admin password it started with.

   ```sh
   cp .env.example .env
   ```

3. Fill in a random password for each database login. `POSTGRES_PASSWORD` is for the admin login, which only the database setup uses; `GROUNDLINE_DB_PASSWORD` is for the login the app uses. Each command fills in its password only if it's blank, so rerunning them doesn't change anything.

   ```sh
   sed -i.bak "s/^POSTGRES_PASSWORD=\$/&$(openssl rand -hex 24)/" .env
   sed -i.bak "s/^GROUNDLINE_DB_PASSWORD=\$/&$(openssl rand -hex 24)/" .env
   rm .env.bak
   ```

   Add `ANTHROPIC_API_KEY` to `.env` too if you'll make live model calls.

4. Start Postgres on `127.0.0.1:5433`:

   ```sh
   make db
   ```

   It returns once Postgres is accepting connections, or fails if it doesn't start. If it stops with `set POSTGRES_PASSWORD in .env` or `set GROUNDLINE_DB_PASSWORD in .env`, `.env` is missing or that password is still blank. For any other failure, `docker compose ps` shows whether the container is running, and `docker compose logs postgres` shows why it stopped.

5. Create the app's login and database, and apply the migrations:

   ```sh
   scripts/db-setup
   ```

   It's safe to rerun, and you need to after every pull that adds a migration.

6. Start the API with auto-reload at http://127.0.0.1:8000 (interactive docs at `/docs` and `/api`):

   ```sh
   make dev
   ```

   To check that the app can reach the database, run `curl http://127.0.0.1:8000/ready` in another terminal. It returns `{"status":"ok"}`, or a 503 if the database isn't answering.

7. Run the tests and checks before you push:

   ```sh
   make check
   ```

## Running the whole stack in Docker

To run Postgres, the database setup and the app together, as they'll run on a server, start the stack at http://127.0.0.1:8001. Stop it with Ctrl+C:

```sh
make docker-run
```

It uses port 8001 so it can run alongside `make dev`. If the app doesn't start, the database setup probably failed; see `docker compose logs setup`.

If you changed the Dockerfile, `compose.yaml`, `.dockerignore`, the dependencies or how the app starts, run the same stack check CI runs. It uses its own containers, passwords and ports, so it doesn't touch your stack:

```sh
make docker-smoke
```

If you run the image with your own `docker run`, publish the port on `127.0.0.1`, as in `-p 127.0.0.1:8001:8000`. A plain `-p 8000:8000` lets anyone on your network reach the API, even past firewalls such as ufw on Linux.

## Deploying to AWS

`make aws-deploy` creates a test deployment in your AWS project: the Compose stack at a commit you choose, on an EC2 instance. Only your computer's public IP can reach it, over plain HTTP, so it's for testing with test data. Real callers need HTTPS first (ALL-90). Each test deployment is disposable. Its data survives container restarts and reboots, until `make aws-destroy`, a deployment of another commit, or the time limit deletes the server and the data with it.

Several developers can share one AWS project, each with their own test deployment. Each test deployment's resources are named and tagged after the `deployment_name` in that developer's `infra/terraform.tfvars`.

The Terraform is in [infra/](infra/), and the server's startup script is [infra/bootstrap.sh.tftpl](infra/bootstrap.sh.tftpl). [infra/README.md](infra/README.md) explains why each AWS setting is what it is, and what needs updating over time.

### Before your first deployment

1. Install Terraform 1.17 or later (`brew install hashicorp/tap/terraform`) and the AWS CLI v2. The server goes in your Region's default VPC, which every new AWS project has; if you deleted yours, `aws ec2 create-default-vpc` creates it again.
2. Sign in with `aws login`. Find your project's Region in AWS Settings, under View all projects > Overview > Additional Info > Region, and set it:

   ```sh
   aws configure set region <your project's Region>
   ```

   The scripts use `AWS_REGION` instead if it's set.
3. Copy the settings file and fill in your `deployment_name`, such as your first name, and your email address. The deployment name has to be different from everyone else's in the AWS project, and the $5-a-month budget alert goes to the email address:

   ```sh
   cp infra/terraform.tfvars.example infra/terraform.tfvars
   ```

   The alert covers your whole AWS project, not only your server, so everyone sharing the project gets it when anyone's server runs long.
4. To make model calls, store an Anthropic API key in Parameter Store as `/groundline/anthropic-api-key`. Without it, the server still starts and `make aws-smoke` still passes, but every model call fails with 503. `read -rs` keeps the Anthropic API key off the screen and out of your shell history:

   ```sh
   read -rs ANTHROPIC_KEY   # paste the key and press Enter
   aws ssm put-parameter --name /groundline/anthropic-api-key --type SecureString --value "$ANTHROPIC_KEY"
   unset ANTHROPIC_KEY
   ```

   Every server in the AWS project reads this one parameter, so only the first person to deploy needs to create it. Only the server reads the Anthropic API key, when it starts. Terraform and the scripts only check that the parameter exists, so the Anthropic API key never lands in Terraform state. To change it, add `--overwrite` to `put-parameter`, then run `make aws-destroy` and `make aws-deploy`.

### Deploying and checking

```sh
make aws-deploy       # create the server, or update it; Terraform shows the plan and asks first
make aws-smoke        # check it from here, without calling Claude
make aws-smoke-live   # also send one real commission check
make aws-status       # show when it terminates itself
make aws-extend       # move that to 8 hours from now (HOURS=n for fewer)
make aws-destroy      # delete it, then check that everything is gone
```

- **`make aws-deploy`** runs the commit you have checked out, which must be pushed to GitHub, since the server clones it from there. To run another, use `REF=<branch, tag or commit> make aws-deploy`. The server takes about 5 to 10 minutes to build the image and start. If your IP changes, rerun `make aws-deploy` with the same commit: only the security group changes. Deploying another commit replaces the server, with its data.
- **The time limit:** each server shuts itself down 8 hours after it first starts, and AWS then terminates it, deleting its disk and its data. `make aws-deploy` prints the time, and `make aws-status` shows how long is left. A redeploy of the same commit doesn't reset the limit, but a redeploy of another commit does, since that creates a new server.
- **`make aws-extend`** moves the deadline to 8 hours from now, or to `HOURS=n` hours from now for fewer. It never moves it earlier, and never more than 8 hours ahead, so a server outlasts the limit only while someone keeps extending it. To change the limit, set `auto_terminate_hours` in `infra/terraform.tfvars` and redeploy; 0 turns it off.
- **After the server terminates itself,** its security group, IAM role, instance profile and budget are still there. They're free, but a later deployment with the same `deployment_name` fails until they're gone. Run `make aws-destroy` to remove them, or `make aws-deploy` for a new server. `make aws-status` and `make aws-smoke` remind you when the server has terminated.
- **`make aws-smoke`** waits up to 15 minutes for `/health` to answer 200. It then creates an API key on the server with `groundline keys create`, through SSM Run Command, and sends an empty `POST /v1/omissions` with an unknown API key (expects 401) and with the new one (expects 422). That shows the server, Postgres, the migrations and authentication work, without spending Anthropic credit. It revokes that API key at the end, even when a check fails. When every check passes, it reminds you to run `make aws-destroy` once you're done.
- **`make aws-smoke-live`** runs the same checks, then sends the shortest test input to `POST /v1/commissions` and expects 200. It costs under a cent. Run it before demos and releases, and after changing a prompt, a preset, the model or how the Anthropic API key reaches the server.

### Keeping an API key for a demo

The smoke test revokes its own API key. To create one that lasts, run `groundline keys create` on the server through SSM Run Command:

```sh
instance="$(terraform -chdir=infra output -raw instance_id)"
command="$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
  --parameters '{"commands":["cd /opt/groundline && docker compose exec -T app groundline keys create --name demo"]}' \
  --query Command.CommandId --output text)"
aws ssm wait command-executed --command-id "$command" --instance-id "$instance"
aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
  --query StandardOutputContent --output text
```

SSM keeps each command's output, so anyone who can read your project's Run Command history can see the API key. It goes when the server does; to revoke it sooner, run `groundline keys revoke <key-id>` the same way.

### Costs

The server costs about $0.024 an hour in us-east-1, about $17 a month: $0.0168 for the `t4g.small` instance, $0.005 for its public IP and $0.002 for its 20 GB disk. Prices differ a little between Regions. The server uses standard CPU credits, so once they run out, it slows down instead of running up extra charges. A server that runs to its 8-hour limit costs about $0.19. Destroy it when you're done anyway: the IAM role, security group and budget it leaves behind are free, but they're clutter. The budget alert emails everyone who has deployed when the project's costs pass $2.50 and $5 in a month.

### Troubleshooting

- **`make aws-smoke` times out:** it prints the end of the server's startup log (`/var/log/groundline-bootstrap.log`) and the containers' status and logs. A failed startup also leaves `/var/lib/groundline/bootstrap-failed`.
- **Requests time out from a new network:** your IP changed. Rerun `make aws-deploy` with the same commit.
- **`make aws-smoke` or `make aws-status` says the server has terminated:** it reached its time limit. Run `make aws-destroy` to remove what it left behind, or `make aws-deploy` for a new server.
- **`make aws-deploy` fails because a role or budget already exists:** someone else in the AWS project uses the same `deployment_name`. Pick another one in `infra/terraform.tfvars`.
- **AWS calls that worked start failing with Access Denied:** your project may have reached its spend limit, which pauses it. Check in AWS Settings, under Billing.

### Tearing down

`make aws-destroy` asks before deleting anything. It then asks AWS whether the instance, security group, IAM role, instance profile and budget are gone, and fails if any is still there or AWS can't say. It leaves the Anthropic API key's parameter, which Terraform didn't create, and prints the command to delete it. Everyone in the AWS project uses that parameter, so delete it only if no one else still deploys.

Terraform state stays in `infra/` on your computer. Delete the server with `make aws-destroy` from the same folder you created it from; another clone doesn't know about it.

## Database

To change a table, edit [src/groundline/db/tables.py](src/groundline/db/tables.py), then add a migration to match (a test fails until they agree). Don't store sources, summaries or results in the database.

```sh
uv run alembic revision -m "add api_keys.owner_id"   # then fill in upgrade() and downgrade()
scripts/db-setup                                      # apply it
```

- **Changing `GROUNDLINE_DB_PASSWORD`:** change it in `.env` and rerun `scripts/db-setup`.
- **Changing `POSTGRES_PASSWORD`:** changing `.env` isn't enough, because Postgres reads it only when the volume is created. Also run `docker compose exec postgres psql -U postgres -c "ALTER ROLE postgres PASSWORD 'NEW'"`.
- **Starting over:** `docker compose down --volumes` deletes the database, with every API key in it.

### Backups

Nothing backs up the database: the Docker volume keeps it across restarts, but whoever runs the server is responsible for backups. It holds only API keys, so losing it means issuing new keys. To back it up and restore it:

```sh
docker compose exec -T postgres pg_dump -U postgres -Fc groundline > groundline.dump
docker compose exec -T postgres pg_restore -U postgres --clean --if-exists -d groundline < groundline.dump
```

To check that a dump restores, restore it into a scratch database and compare the row count with `SELECT count(*) FROM api_keys` on the live database:

```sh
docker compose exec -T postgres createdb -U postgres restore_check
docker compose exec -T postgres pg_restore -U postgres -d restore_check < groundline.dump
docker compose exec -T postgres psql -U postgres -d restore_check -c "SELECT count(*) FROM api_keys"
docker compose exec -T postgres dropdb -U postgres restore_check
```

A major Postgres upgrade, such as 18 to 19, uses the same commands: back up, check the dump restores, change the image in `compose.yaml`, delete the volume, start the stack so setup creates the database again, then restore. Check the dump before deleting the volume, because a bad dump can't be fixed after that.

## Tests

There are three test suites. They differ in what they use for Postgres and Claude, so run each one when your change calls for it:

- **`make test-fast`** runs every test except the two groups below, with fakes in place of Postgres and Claude: a fake Claude returns recorded replies, and API keys are kept in memory. It covers the pipeline steps, the API's endpoints and errors, the CLI, the schemas, the replayed preset runs, and the checks of `compose.yaml`, the Terraform files and the AWS scripts. It needs nothing running and costs nothing. Run it before every push; `make check` runs it too.
- **`make test-db`** starts Postgres, then runs the tests that need a real one: storing API keys and checking them on requests, the migrations and the schema, `/ready`, and `groundline keys`. Run it when you change the tables, migrations, API keys or authentication. Each run creates its own throwaway database and drops it afterwards.
- **`make test-live`** runs the tests that call the real Anthropic API, for about $0.09 a run. Run it when you change what groundline sends to the model: a prompt, the rubric, a preset, model settings or the Anthropic client.

None of them starts the Docker stack, runs `terraform test` or touches AWS: `make docker-smoke`, `make infra-check` and `make aws-smoke` do that.

CI runs the fast and database tests on every PR, but never the live tests.

If you change a prompt, the rubric or a step's logic, expect `tests/test_parity.py` and `tests/test_parity_commissions.py` to fail. They replay recorded runs of the measured presets, so a failure means `omission_all_opus` or `commission_quick` no longer matches its measured results.

## Lint, format and type checks

`make check` runs ruff, pyright in strict mode, the large-file check and the tests, without changing files. CI runs it too, along with the database tests (`make test-db`), the Docker stack check (`make docker-smoke`) and the Terraform checks (`make infra-check`). `make infra-check` needs Terraform, but no AWS credentials: its tests plan against a mock AWS provider. Run it when you change `infra/`. `make fix` applies ruff's fixes and formatting. Pyright errors need fixing by hand.

The `pre-commit` hook runs the same checks on staged files. If ruff fails, run `make fix` and stage the result.

## Editor schemas

The YAML profiles, pipeline specs and presets are checked against generated JSON schemas. After changing the profile model or a stage implementation's settings, regenerate them (a test fails until you do):

```sh
uv run python -m groundline.profiles.schema
uv run python -m groundline.presets.schema
```

## API docs

The docs page is generated from the request and response models in `src/groundline/api/`. Each model carries an example in `json_schema_extra`, and `tests/test_api_docs.py` checks that every example still fits its model. FastAPI leaves `null` values out of examples in the docs, so keep them out of the examples too. The app's `description` in `src/groundline/app.py` holds the versioning policy shown at the top of the page.

## Protecting an endpoint

To require an API key on a route, add `dependencies=[Depends(require_api_key)]` from `groundline.auth`, and `responses=PROTECTED_RESPONSES` from `groundline.api.common` to document the 401 and 503. `POST /v1/omissions` and `POST /v1/commissions` pass `responses=check_responses(...)` instead, which documents every error status a check can answer, with a labeled example of each. It builds the route's preset, so the 502 examples name the first stage that calls a model, at that model's price, and the 503 example lists the stages that need the Anthropic API key. To use the key's metadata in the handler, take it as a parameter instead: `api_key: Annotated[APIKeyInfo, Depends(require_api_key)]`.

Every route needs it except probes such as `/health` and `/ready` and the docs pages ([ADR 0012](docs/adr/0012-only-probes-and-docs-pages-are-public.md)). A test in `tests/test_api_auth.py` lists the public probes and fails if any other route lacks an API key.

## Layout

```
.agents/skills/            agent skills (see AGENTS.md)
.claude/skills/            links to .agents/skills/, so Claude Code finds the same skills
Dockerfile                 the app image
compose.yaml               the stack: Postgres, the setup service and the app
src/groundline/
  core/                    the pipeline framework: steps, registry, specs, runner, cache, costs
  clients/                 the Anthropic client
  db/                      the database: tables, migrations, connection check, setup
  evaluators/              what the checks share, and the registry of built-in checks
  evaluators/omissions/    the omission check
  evaluators/commissions/  the commission check
  profiles/clinical/       the clinical profile: prompts and severity rubric
  presets/                 validated pipeline specs (omission_all_opus, commission_quick)
  evaluate.py              evaluate(): the entry point the CLI and API share
  api_keys.py              API-key creation, revocation and authentication
  auth.py                  the Bearer authentication dependency
  cli.py, keys_cli.py      the groundline command and its keys subgroup
  api/, app.py             the API's endpoints and the FastAPI app
infra/                     Terraform for AWS test deployments, the startup script, and their tests
scripts/                   db-setup, the stack check behind make docker-smoke, and the AWS
                           scripts behind make aws-deploy, aws-smoke, aws-smoke-live and aws-destroy
tests/                     the tests
```

## Vocabulary, decisions and commits

Read [CONTEXT.md](CONTEXT.md) (the glossary) and [docs/adr/](docs/adr/) (the decisions) before changing the design. [AGENTS.md](AGENTS.md) has the rules for updating them and for commit messages (Conventional Commits).

The `commit-msg` hook adds a `Co-authored-by` line for every other team member. It recognizes you by `git config user.email`, so check that it matches your email in [.githooks/commit-msg](.githooks/commit-msg).

## Upgrading uv

When a new uv minor release comes out (e.g. 0.14):

1. In [pyproject.toml](pyproject.toml), set `required-version = ">=0.14.0,<0.15"` and, in `[build-system]`, `requires = ["uv_build>=0.14.0,<0.15.0"]`. Keep the two on the same minor.
2. Update the [Dockerfile](Dockerfile)'s first `FROM` line to the new uv tag and digest. Get the digest with `docker buildx imagetools inspect ghcr.io/astral-sh/uv:<version>-python3.13-trixie-slim`.
3. Upgrade your uv to a version in the new range: `uv self update 0.14.0` for the standalone installer, or `pip install 'uv>=0.14,<0.15'`. With Homebrew, `brew upgrade` may install a uv past the range; if so, `brew uninstall uv` and use the standalone installer.
4. Run `make check` and `make docker-smoke`, then push, and tell the team to upgrade too.
