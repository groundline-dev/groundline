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

`tests/https_smoke.py` is an executable integration harness, invoked by `make https-smoke`, rather than a pytest suite. It lives beside the other Docker smoke harness and uses the runtime Python image pinned in `Dockerfile`.

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

`make check` runs ruff, pyright in strict mode, the large-file check, secret checks and the tests, without changing files. CI runs it too, along with the database tests (`make test-db`), the Docker stack check (`make docker-smoke`), the Terraform checks (`make infra-check`) and the local certificate lifecycle checks (`make https-smoke`). `make infra-check` needs Terraform, but no AWS credentials: its tests plan against a mock AWS provider. Run it when you change `infra/`. `make fix` applies ruff's fixes and formatting. Pyright errors need fixing by hand.

The `pre-commit` hook runs lint, format, type, large-file and secret checks on staged changes; it does not run pytest. If ruff fails, run `make fix` and stage the result.

### Secret checks

`make setup` enables the commit hook. Its first run downloads and builds the pinned Gitleaks release through pre-commit, which can take longer than later checks. Gitleaks scans staged changes for known credential formats and other likely secrets. It cannot identify every password, so a separate filename check also blocks files whose names start with `.env` or end with `.env`, in any directory. This includes empty files, renamed files and ignored files added with `git add -f`.

Use `.env.example` for a template containing placeholders. That exact filename is allowed, but Gitleaks still scans its contents. Local environment files stay ignored by Git.

`make check` additionally scans the current contents of all tracked files, including unstaged edits. It excludes untracked files, local environment files and caches. To run just that scan:

```sh
uv run pre-commit run gitleaks-tracked --hook-stage manual --all-files
```

The `secrets` CI job scans all fetched Git history, including secrets removed by later commits. It uses the Gitleaks CLI without a license key or repository secret. Findings are redacted in the output. A failed CI check happens after a push: it does not prevent public exposure, and bypassing a local hook can publish a secret before CI reports it.

For an intentional dummy value that Gitleaks flags, add `gitleaks:allow` in a comment on that line and explain why the value is harmless. For an existing historical false positive, copy only its fingerprint into `.gitleaksignore`, with a comment explaining the review. A fingerprint identifies a finding; do not copy the credential or matched source line. Prefer these narrow exceptions over excluding a file, rule or commit. A comment does not bypass the filename check: rename templates to `.env.example`. Historical filename findings use `COMMIT:PATH:env-file:0` fingerprints and can be exempted after confirming the file was harmless.

If a real secret reaches GitHub, revoke and replace it first, then tell the team without sharing its value. Deleting the file, closing a PR or rewriting history does not invalidate an exposed credential. Coordinate any history cleanup with the team; GitHub's [sensitive-data removal guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) explains the remaining copies and cleanup steps.

When updating Gitleaks, update both the commit SHA and release comment in `.pre-commit-config.yaml` and `GITLEAKS_VERSION` in `.github/workflows/ci.yml`. The CI download verifies the archive against the release's checksum file. Dependabot updates the hook pin; review the matching CLI version in the same change.

`tests/test_secret_checks.py` exercises the helper with a stand-in scanner as part of `make test-fast`. The executable integration harness, `tests/secrets_smoke.py`, checks the actual scanner against disposable repositories. CI runs it before the history scan. To reproduce those checks locally with the matching Gitleaks release on `PATH`:

```sh
python3 tests/secrets_smoke.py
python3 scripts/check_secrets.py history
```

The [history audit record](docs/security-audit.md) states the scanned scope and findings.

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
