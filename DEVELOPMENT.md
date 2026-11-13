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

## Timeouts and outage retries

Timeout and retry defaults live in `src/groundline/settings.py`. No environment
variables or `.env` entries are needed to use them. The table below documents the
current defaults; `.env.example` contains commented examples of optional overrides.

To override a value, set its `GROUNDLINE_` variable in the shell or deployment
environment, or uncomment it in `.env`. Existing environment values take precedence.
For example, `GROUNDLINE_CHECK_TIMEOUT_SECONDS=180 make dev` changes the check
limit for that run. The same assignment works with `make docker-run`.

Compose's key-only environment entries forward supplied overrides and omit unset
ones, so the container uses the same application defaults. It never receives the
whole `.env` file or the admin credential. `ANTHROPIC_API_KEY`, `DATABASE_URL` and
`DATABASE_ADMIN_URL` retain their existing names.

If you copied an earlier `.env.example`, comment out or remove timeout/retry entries
you do not intend to override; existing uncommented values remain explicit overrides.

All durations below are positive finite seconds. Attempts must be a positive
integer. Defaults require no additional setup.

| Environment setting | Default | Bounds |
|---|---:|---|
| `GROUNDLINE_CHECK_TIMEOUT_SECONDS` | 120 | Whole check, starting before API authentication; also existing CLI and Python evaluation paths |
| `GROUNDLINE_DB_AUTH_TIMEOUT_SECONDS` | 5 | Entire authentication operation |
| `GROUNDLINE_DB_CONNECT_TIMEOUT_SECONDS` | 5 | Database connection establishment |
| `GROUNDLINE_DB_POOL_TIMEOUT_SECONDS` | 5 | Waiting for a pooled connection |
| `GROUNDLINE_DB_CHECK_TIMEOUT_SECONDS` | 5 | Connection acquisition including pre-ping; also disposal of owned readiness engines |
| `GROUNDLINE_DB_QUERY_TIMEOUT_SECONDS` | 5 | PostgreSQL statements including lock waits; API-key transactions also have this allowance after acquisition, including commit |
| `GROUNDLINE_DB_READINESS_TIMEOUT_SECONDS` | 5 | Whole startup/readiness probe, for owned and borrowed engines |
| `GROUNDLINE_MODEL_TIMEOUT_SECONDS` | 60 | Each model attempt |
| `GROUNDLINE_MODEL_CONNECT_TIMEOUT_SECONDS` | 5 | Model connection establishment |
| `GROUNDLINE_MODEL_RETRY_TIMEOUT_SECONDS` | 120 | One model call's attempts and waits together |
| `GROUNDLINE_MODEL_MAX_ATTEMPTS` | 3 | Total attempts, including the original; default permits two outage retries |
| `GROUNDLINE_MODEL_RETRY_BASE_SECONDS` | 1 | Initial full-jitter backoff cap |
| `GROUNDLINE_MODEL_RETRY_CAP_SECONDS` | 8 | Maximum backoff cap |

Each nested operation is capped by the remaining whole-check allowance; another
stage or request for correction never resets the deadline. The model retry budget
is independent: it can expire first if reduced, or if the check allowance is
increased. Caller-supplied model clients receive the check deadline through task
cancellation and remain responsible for their own outage retries. These are
internal mechanisms, not new public library configuration arguments or exports.
Cancellation is cooperative; code that suppresses cancellation cannot be forcibly
stopped. Allow a short cleanup interval after expiry for connections to close.

Groundline disables Anthropic SDK retries and retries 408, 409, 429, 5xx,
connection errors, and temporary timeouts in one layer. Permanent errors are not
retried. Full-jitter waits are uniform from zero to the current cap: 1, 2, 4,
then 8 seconds at default settings. Valid `retry-after-ms` takes precedence over
`Retry-After` seconds or HTTP dates and sets a minimum wait. Malformed values fall
back to the next valid header or backoff. `x-should-retry=false` vetoes a retry;
true never overrides permanent errors, cancellation, attempts, or deadlines.
A wait that cannot fit stops immediately on the upstream error (502) unless an
allowance has expired (504). The final permitted attempt timing out returns 504
even if whole-check time remains. Authentication timeouts return 503.

A request for correction follows an answered but unusable model reply. It remains
separate from outage retries, shares the check deadline, and may incur another
charge. Interrupted attempts may bill without returning usage; failure cost
reports describe recorded usage only. A late retry can consume the remaining
time and add charges without yielding a result. Use an HTTP client timeout longer
than the configured check allowance plus transfer time.

The initial defaults were checked against historical evaluator timing records:
reconstructed omission model-call totals had p95 86.92 seconds and maximum 91.97;
extraction alone reached 42.27 seconds. Quick commission totals reached 18.42
seconds. These exclude authentication and application overhead and are not fresh
end-to-end benchmarks; monitor real durations before tuning the defaults.

Database acquisition uses the smallest connection/check/pool allowance, with a
separate SQLAlchemy pool timeout. libpq's integer `connect_timeout` has a two-second minimum; async
acquisition boundaries enforce shorter configured allowances. PostgreSQL
`statement_timeout` is set per application connection, never globally. SQLAlchemy
rolls back failed transactions where possible or discards unusable connections;
connection maintenance does not replay failed application transactions.

Database operations are never automatically retried. API-key CLI errors exit 2;
Python callers receive a sanitized storage exception. A failed commit can leave
the outcome unknown: inspect the database before repeating a write. API-key names
are not unique. A key created during an uncertain commit may have no recoverable
plaintext; identify the record and revoke it if appropriate. There is no automatic
reconciliation or guarantee that repetition is safe.

To reproduce the failure paths without paid model calls:

```sh
uv run pytest -v tests/test_model_retry.py tests/test_anthropic_client.py tests/test_deadline.py tests/test_api_errors.py
make test-db
make docker-smoke
```

The offline tests inject 529 responses, transport timeouts, and hung operations;
they verify recovery, cancellation, sanitized 502/504 responses, and the shared
authentication deadline. Database tests use throwaway databases to exercise query
and lock timeouts, pool exhaustion, rollback, and subsequent authentication.
The Docker smoke test uses its existing mock provider for both successful checks.


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

The smoke test uses `compose.smoke.yaml` to add a mock Anthropic HTTP service, dummy credentials and an explicit Anthropic base URL pointing to the mock. It keeps the normal app image and startup, checks health/readiness and API-key persistence, then calls both evaluators and checks their findings, preset identity and model-call counts. Presets, prompts and the rubric load from the built image; only the smoke-test support script is mounted. It needs no real Anthropic API key or paid model calls, and CI runs the same script. Health/readiness and authentication probes run on the host through the published Docker port; evaluator checks run inside the app container against the same mock service.

If you run the image with your own `docker run`, publish the port on `127.0.0.1`, as in `-p 127.0.0.1:8001:8000`. A plain `-p 8000:8000` lets anyone on your network reach the API, even past firewalls such as ufw on Linux.

## Deploying to AWS

Test deployments are for internal testing by groundline's developers only. A test deployment runs the Compose stack, at a commit you choose, on an EC2 server in your AWS project. Only your computer's IP can reach it, so it's for test data only, and it deletes itself, with its data, after 8 hours.

**1. Once, before your first deployment:** get everything [Before you start](docs/deploying-to-aws.md#before-you-start) lists. The wizard can't do these for you:

- an AWS project, either your own or the Groundline project
- an Anthropic API key
- to deploy your own branch, a public GitHub repository that has it, such as your fork

**2. Each time you test:**

```sh
make aws-up        # choose the project and the code, deploy, and check the server
make aws-destroy   # when you're done
```

[docs/deploying-to-aws.md](docs/deploying-to-aws.md) explains what `make aws-up` asks, the other `make aws-*` commands, costs and troubleshooting. To change the Terraform or the scripts, see [infra/README.md](infra/README.md).

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

### API behavior suite

`make test-api` runs the focused API tests with a named pass/fail result for each endpoint and scenario. It checks authentication, validation, evaluator responses, settings/SDK integration, documentation and safe errors. No running server, database, real API key or network access is needed.

Optionally save a JUnit XML report:

```sh
make test-api PYTEST_ARGS="--junitxml=.pytest_cache/api-results.xml"
```

`make test-fast` and CI also run these tests. The offline suite uses fake API-key storage; actual PostgreSQL persistence, expiry and revocation are covered by `make test-db`, which CI runs separately.

Anthropic SDK DEBUG logging includes raw prompts. Privacy assertions capture groundline logs at DEBUG and dependencies at normal verbosity; do not enable third-party debug logging when handling sensitive content.

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
