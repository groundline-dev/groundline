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

Set the two optional overrides below in `.env` or the shell; shell values take
precedence. Leave them unset to use the defaults in
[`_settings.py`](src/groundline_core/_settings.py).

### Overall check limit

The 120-second limit covers the entire check, including authentication, model
calls and retries. It applies to the API, CLI and Python evaluations.

To change the limit, add this line to `.env`:

```dotenv
GROUNDLINE_CHECK_TIMEOUT_SECONDS=180
```

### Model retries

Groundline retries temporary model failures, such as rate limits, connection
errors and timeouts. Each model call allows up to three attempts, including the
first request. Groundline does not retry permanent errors or database operations.

To disable outage retries, add this line to `.env`:

```dotenv
GROUNDLINE_MODEL_MAX_ATTEMPTS=1
```

If the model answers in the wrong format, groundline asks it to answer again
in the required format. This can happen even with `GROUNDLINE_MODEL_MAX_ATTEMPTS=1`:
that setting controls retries after temporary failures, not requests to fix
the format of an answer.

A higher attempt limit allows more retries, but does not extend the overall check
limit.

### Other limits

Each model attempt has a 60-second limit. One model call, including all its
attempts and retry waits, has a 120-second limit. Increasing the overall check
limit does not increase either limit. A model call stops sooner if the overall
check runs out of time.

Database operations also have their own time limits. The model and database
limits are defined on the `Settings` class in
[`src/groundline_core/_settings.py`](src/groundline_core/_settings.py).

See [API statuses and errors](README.md#statuses-and-errors) for response codes
and the cost implications of failed checks. Python configuration behavior is
documented on [`evaluate()`](src/groundline_core/_evaluate.py) and
[`AnthropicClient`](src/groundline_core/_clients/anthropic.py).

## Request size limits

The API limits request bodies to keep a single upload from using too much memory.
The same limit applies to every HTTP path and method, including public endpoints,
unknown routes and new routes added later.

Both check endpoints also limit the number of Unicode characters in source and
summary after JSON decoding. The body limit counts the bytes sent, including
JSON structure and escaping. These limits control how much text the API accepts. Models have their own
limits, measured in tokens rather than characters, and the instructions sent
with your text also take up space. An accepted request can therefore still be
too large for the selected model.

CLI and direct Python evaluation do not go through the HTTP API, so these request
limits do not apply to them. The selected model's limits still apply.

A declared oversized body can return **413 Content Too Large** before
authentication, **404 Not Found** or **405 Method Not Allowed**. Streamed bodies
are limited as the application reads them; routes that do not read a body do not
buffer it to check its size.

| Setting | Unit | Default |
| --- | --- | --- |
| `GROUNDLINE_MAX_BODY_BYTES` | Total body bytes | 5,242,880 (5 MiB) |
| `GROUNDLINE_MAX_SOURCE_CHARS` | Decoded source characters | 200,000 |
| `GROUNDLINE_MAX_SUMMARY_CHARS` | Decoded summary characters | 200,000 |

For `make dev` or Docker Compose, add the settings you want to change to `.env`:

```dotenv
GROUNDLINE_MAX_BODY_BYTES=10485760
GROUNDLINE_MAX_SOURCE_CHARS=100000
```

This example allows a 10 MiB body and up to 100,000 source characters, while
leaving the summary limit at its default. Exported shell values take precedence
over `.env`, just as they do for the check timeout and retry settings.

Setting values must be positive ASCII decimal integers, without spaces, signs,
underscores, fractions or exponents. Invalid configuration stops API startup
with an error naming the setting and omitting its rejected value. Missing or
empty values select the code defaults.

After changing `.env` or exported values, restart `make dev` or recreate the
Compose app container to apply them:

```sh
docker compose up -d --force-recreate app
```

Body and field allowances are independent. Raising a field allowance may require
raising the body allowance too; a deliberately tighter body allowance can reject
otherwise valid fields. Oversized bodies return **413 Content Too Large**;
overlong fields return **422 Unprocessable Content**, without echoing input.

This limit prevents a single upload from being too large. The server still needs
memory to process requests, and several requests can arrive at once, so its total
memory use can exceed this limit.

The running server's `/docs` (also `/api`) shows its current limits: body bytes
in the 413 response description, and character limits in the `CheckRequest` schema.
For AWS restrictions, see [deployment limits](docs/deploying-to-aws.md#request-size-limits).

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

See the [deployment guide](docs/deploying-to-aws.md) for access modes, prerequisites and the disposable deployment lifecycle.

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

To change a table, edit [src/groundline_core/db/tables.py](src/groundline_core/db/tables.py), then add a migration to match (a test fails until they agree). Don't store sources, summaries or results in the database.

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

`tests/https_smoke.py` is an executable integration harness, invoked by `make https-smoke`, rather than a pytest suite. It lives beside the other Docker smoke harness and uses the runtime Python image pinned in `Dockerfile`.

There are three test suites. They differ in what they use for Postgres and Claude, so run each one when your change calls for it:

- **`make test-fast`** runs every test except the two groups below, with fakes in place of Postgres and Claude: a fake Claude returns recorded replies, and API keys are kept in memory. It covers the pipeline steps, the API's endpoints and errors, the CLI, the schemas, the replayed preset runs, and the checks of `compose.yaml`, the Terraform files and the AWS scripts. It needs nothing running and costs nothing. Run it before every push; `make check` runs it too.
- **`make test-db`** starts Postgres, then runs the tests that need a real one: storing API keys and checking them on requests, the migrations and the schema, `/ready`, and `groundline-core keys`. Run it when you change the tables, migrations, API keys or authentication. Each run creates its own throwaway database and drops it afterwards.
- **`make test-live`** runs the tests that call the real Anthropic API, for about $0.09 a run. Run it when you change what groundline sends to the model: a prompt, the rubric, a preset, model settings or the Anthropic client.

None of them starts the Docker stack, runs `terraform test` or touches AWS: `make docker-smoke`, `make infra-check` and `make aws-smoke` do that.

`make core-check` builds the package and installs it with no extras, once with pip and once with uv pip, then checks that the library loads without the server or CLI packages and that the command and the API say which extra to install. It also type-checks `scripts/library_typing_check.py`, a file that uses only the public names, with pyright strict against each install, so a public name or result field whose type callers can't see fails it. Run it when you change the dependencies in `pyproject.toml`, what `groundline_core` imports when it loads, or the public interface. CI runs it on every PR.

CI runs the fast and database tests on every PR, but never the live tests.

`tests/https_smoke.py` is an executable integration harness, invoked by `make https-smoke`, rather than a pytest suite. It lives beside the other Docker smoke harness and uses the runtime Python image pinned in `Dockerfile`.

If you change a prompt, the rubric or a step's logic, expect `tests/test_parity.py`, `tests/test_parity_commissions.py` and `tests/test_parity_commissions_lean.py` to fail. They replay recorded runs of the measured presets, so a failure means `omission_all_opus`, `commission_quick` or `commission_reverse_lean` no longer matches its measured results.

### API behavior suite

`make test-api` runs the focused API tests with a named pass/fail result for each endpoint and scenario. It checks authentication, validation, evaluator responses, settings/SDK integration, documentation and safe errors. No pre-existing server, database, real API key or internet access is needed. The upload test starts a temporary uvicorn server and requires loopback networking.

Optionally save a JUnit XML report:

```sh
make test-api PYTEST_ARGS="--junitxml=.pytest_cache/api-results.xml"
```

`make test-fast` and CI also run these tests. The offline suite uses fake API-key storage; actual PostgreSQL persistence, expiry and revocation are covered by `make test-db`, which CI runs separately.

Anthropic SDK DEBUG logging includes raw prompts. Privacy assertions capture groundline logs at DEBUG and dependencies at normal verbosity; do not enable third-party debug logging when handling sensitive content.

## Lint, format and type checks

`make check` runs ruff, pyright in strict mode (the SDK's generated code is the exception, see [The SDK](#the-sdk)), the large-file check, secret checks and the tests, without changing files. CI runs it too, along with the database tests (`make test-db`), the SDK checks (`make sdk-check` and `make sdk-install-check`, see [The SDK](#the-sdk)), the Docker stack check (`make docker-smoke`), the Terraform checks (`make infra-check`) and the local certificate lifecycle checks (`make https-smoke`). `make infra-check` needs Terraform, but no AWS credentials: its tests plan against a mock AWS provider. Run it when you change `infra/`. `make fix` applies ruff's fixes and formatting. Pyright errors need fixing by hand.

The `pre-commit` hook runs lint, format, type, large-file and secret checks on staged changes; it does not run pytest. If ruff fails, run `make fix` and stage the result.

### Secret checks

Secrets are checked in three places:

- **Commit hook**, enabled by `make setup`: scans staged changes, and blocks filenames starting with `.env` or ending in `.env`, even if the file is empty or force-added. Use `.env.example` for templates; its filename is allowed, but its contents are still scanned. On its first run, pre-commit downloads and builds Betterleaks, so expect a delay.
- **`make check`**: also scans the current contents of all tracked files, including unstaged edits, but not untracked files.
- **CI** (`secrets` job): scans all fetched Git history, including secrets removed by later commits, and redacts findings. It runs after a push, so it can't stop a secret reaching the public repository; only the commit hook can.

To run one check on its own: the tracked-files scan, CI's tests of the checks against the real scanner, or CI's history scan:

```sh
uv run pre-commit run secrets-tracked --hook-stage manual --all-files
uv run pre-commit run secrets-smoke --hook-stage manual --all-files
uv run pre-commit run secrets-history --hook-stage manual --all-files
```

**False positives.** For a harmless dummy value, add `betterleaks:allow` in a comment on that line and explain why it is harmless. For a confirmed historical false positive, add its fingerprint, which starts with the commit, to `.betterleaksignore` with an explanation; do not copy the secret or source line. Both are narrower than excluding a file, rule or commit. Neither bypasses the filename check: rename templates to `.env.example`.

**If a real secret reaches GitHub**, revoke and replace it first, then tell the team without sharing its value. Deleting it or rewriting history does not invalidate it. Coordinate cleanup with the team; see GitHub's [sensitive-data removal guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

**Updating Betterleaks.** The pin in `.pre-commit-config.yaml` is the only one; CI runs the same hooks. Dependabot updates it within v1. Moving to v2 is manual: it drops flags `scripts/check_secrets.py` uses and changes fingerprints.

## Editor schemas

The YAML profiles, pipeline specs and presets are checked against generated JSON schemas. After changing the profile model or a stage implementation's settings, regenerate them (a test fails until you do):

```sh
uv run python -m groundline_core.profiles.schema
uv run python -m groundline_core.presets.schema
```

## API docs

The docs page is generated from the request and response models in `src/groundline_core/api/`. Each model carries an example in `json_schema_extra`, and `tests/test_api_docs.py` checks that every example still fits its model. FastAPI leaves `null` values out of examples in the docs, so keep them out of the examples too. The app's `description` in `src/groundline_core/app.py` holds the versioning policy shown at the top of the page.

## The SDK

The SDK is the `groundline-client` package in `packages/groundline-client/`, a member of the repo's uv workspace. Most of its code is generated from the API's OpenAPI description by [openapi-python-client](https://github.com/openapi-generators/openapi-python-client), and committed in `src/groundline_client/_generated/`. Don't edit that folder by hand ([ADR 0019](docs/adr/0019-the-sdk-is-generated-with-openapi-python-client.md)).

After changing a route, a model or anything else in the API's description, regenerate the SDK and commit the changed files:

```sh
make generate-sdk
```

It writes the API's description to `packages/groundline-client/openapi.json`, clears `_generated/`, and runs the generator, which is pinned in `uv.lock`. Changing the app's version also changes `openapi.json`, so regenerate then too. Upgrading the generator means changing its pin in `packages/groundline-client/pyproject.toml`, running `uv lock`, and regenerating, in one PR.

CI guards the SDK with two jobs. `make sdk-check` regenerates the SDK into a temporary folder and fails if the result differs from the files on disk. It changes nothing in your working tree, so you can run it any time. `make sdk-install-check` builds the wheel, installs it into clean environments with pip and with uv pip, and fails if it pulls in `groundline-core`'s packages (the pipelines, the Anthropic SDK or the server). `tests/test_sdk_spec.py` checks that `openapi.json` is what the export writes from the app (the API's description with the default limits and without request-field defaults, so `/docs` shows defaults that the file doesn't), and the tests in `packages/groundline-client/tests/` check that every endpoint has a generated function and models.

The hand-written layer is `GroundlineClient` in `client.py` (the Client in `CONTEXT.md`). It reads the URL and API key from its arguments or from `GROUNDLINE_API_URL` and `GROUNDLINE_API_KEY`, and has a sync and an `_async` method for each operation. It also retries temporary failures and limits how long a call takes: the rules are in `_retry.py`, and `_http.py` holds the two `httpx2` clients (sync and async) that apply them by overriding `send`, which keeps proxies from the environment working. Its options and their defaults are in the SDK README. To add an operation:

1. Add its sync and `_async` methods to `GroundlineClient`, with the same parameters as the generated `sync` and `asyncio` functions.
2. Export its models from `__init__.py` (the imports and `__all__`).
3. Add it to the operation lists in `tests/test_client_requests.py` and `tests/test_endpoints.py`.

Two tests fail if you miss a step: one for a generated operation with no method (and `_async` twin), and one for a method whose parameters differ from the generated function's. The generated client prints the API key in its `repr`, so `client.py` wraps it in a subclass that doesn't, and `tests/test_client_secrecy.py` checks that the key stays out of `repr`, errors, tracebacks (with their local variables) and logs. The tests run against a fake server (`tests/conftest.py`), not the real API.

The generated code is exempt from ruff's style and formatting rules, since the generator formats it, and pyright checks it at the basic level. Only the hand-written code (every module directly in `src/groundline_client/`) and the tests are checked in strict mode (`uv run pyright -p packages/groundline-client`, which pre-commit runs).

## Protecting an endpoint

To require an API key on a route, add `dependencies=[Depends(require_api_key)]` from `groundline_core.auth`, and `responses=PROTECTED_RESPONSES` from `groundline_core.api.common` to document the 401 and 503. `POST /v1/omissions` and `POST /v1/commissions` pass `responses=check_responses(...)` instead, which documents every error status a check can answer, with a labeled example of each. It builds the route's default preset, so the 502 examples name the first stage that calls a model, at that model's price, and the 503 example lists the stages that need the Anthropic API key. For `/v1/commissions` that is `commission_quick`; a `commission_reverse_lean` request names its own stages. To use the key's metadata in the handler, take it as a parameter instead: `api_key: Annotated[APIKeyInfo, Depends(require_api_key)]`.

Every route needs it except probes such as `/health` and `/ready` and the docs pages ([ADR 0012](docs/adr/0012-only-probes-and-docs-pages-are-public.md)). A test in `tests/test_api_auth.py` lists the public probes and fails if any other route lacks an API key.

## Layout

```
.agents/skills/            agent skills (see AGENTS.md)
.claude/skills/            links to .agents/skills/, so Claude Code finds the same skills
Dockerfile                 the app image
compose.yaml               the stack: Postgres, the setup service and the app
src/groundline_core/       a leading underscore marks the library's internals (ADR 0018)
  _core/                   the pipeline framework: steps, registry, specs, runner, cache, costs
  _clients/                the Anthropic client
  db/                      the database: tables, migrations, connection check, setup
  _evaluators/             what the checks share, and the registry of built-in checks
  _evaluators/omissions/   the omission check
  _evaluators/commissions/ the commission check
  profiles/clinical/       the clinical profile: prompts, severity rubric, disclaimer sentence
  presets/                 validated pipeline specs (omission_all_opus, commission_quick,
                           commission_reverse_lean)
  __init__.py              the public interface: the names library callers import
  _evaluate.py             evaluate(): the entry point the CLI and API share
  _result.py, _findings.py the result evaluate() returns, and how findings are built from a run
  _disclaimer.py           the disclaimer every result carries
  _settings.py             secrets and settings, from the environment or a .env file
  api_keys.py              API-key creation, revocation and authentication
  auth.py                  the Bearer authentication dependency
  command.py               where the groundline-core command starts: checks for the cli extra
  cli.py, keys_cli.py      the groundline-core command and its keys subgroup
  extras.py                what to say when the server or cli extra isn't installed
  api/, app.py             the API's endpoints and the FastAPI app
infra/                     Terraform for AWS test deployments, the startup script, and their tests
packages/groundline-client/  the SDK: its pyproject.toml, openapi.json, src/ (the generated code is
                           in _generated/) and tests/
scripts/                   db-setup, the stack check behind make docker-smoke, the install check
                           behind make core-check, make generate-sdk and the SDK install check, and the AWS
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
