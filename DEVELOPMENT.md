# Developing groundline

How to work on groundline itself. For what it does and how to use it, start with the [README](README.md).

## Setup

Follow the README's [Setup](README.md#setup) section first: `make setup`, and an API key in `.env` if you'll make live calls. `make setup` installs the dependencies and enables the shared git hooks in [.githooks/](.githooks/).

## Running the API

Start the API with auto-reload at http://127.0.0.1:8000 (interactive docs at `/docs`):

```sh
make dev
```

Use this for day-to-day work. It reloads when you edit the source, which the container doesn't.

The README's [Using the API](README.md#using-the-api) describes the endpoints. Each endpoint has a request and response model of its own in `src/groundline/api/`, so refactoring the pipeline's models can't change the API by accident.

### In Docker

The [Dockerfile](Dockerfile) builds a production-style image. You need [Docker](https://docs.docker.com/get-started/get-docker/) installed and running.

Build the image:

```sh
make docker-build
```

Run it at http://127.0.0.1:8001. To use another port, add `DOCKER_PORT=<port>`. If you have a `.env`, it's mounted read-only into the container, so the app reads your API key the same way as under `make dev`. Stop it with Ctrl+C:

```sh
make docker-run
```

- **No API key in the container:** `.env` is mounted only if it exists when the container starts. If the app tells you to copy `.env.example` to `.env` and names `/home/groundline/.env`, that's the path inside the container. Create `.env` in the repo folder, then stop the container and run `make docker-run` again.
- **No database yet:** inside the container, `localhost` is the container itself, so the app logs that it can't connect to the database at `localhost:5432` and keeps running. Nothing uses the database yet. A Compose file that runs Postgres next to the app is planned (ALL-70).
- **Running the image with your own `docker run`:** include `127.0.0.1` in the port, as `make docker-run` does: `-p 127.0.0.1:8001:8000`. Without an IP, as in `-p 8000:8000`, Docker listens on every network interface, so anyone on your network can reach the API. On Linux, this also gets past firewalls such as ufw. The API has no authentication yet, and every request spends Anthropic credit.

Build the image, start it and check that `/health` returns 200. CI runs the same check on every PR. Run it before pushing if you changed the Dockerfile, `.dockerignore`, the dependencies, or how the app starts. It runs on a free port Docker picks, so it works while `make dev` is running:

```sh
make docker-smoke
```

## Database

The API keeps its operational data, starting with API keys, in PostgreSQL 18. It never holds sources, summaries or results ([ADR 0009](docs/adr/0009-the-database-holds-no-patient-data.md)). The `groundline keys` commands require it; the omission and commission commands and Python evaluation functions do not. For now each developer runs Postgres natively; Docker is planned.

Install and start PostgreSQL 18:

```sh
# macOS
brew install postgresql@18
brew link postgresql@18   # Homebrew doesn't put versioned Postgres on the PATH by itself
brew services start postgresql@18

# Ubuntu/Debian (with the PostgreSQL apt repository: https://www.postgresql.org/download/linux/)
sudo apt install postgresql-18
sudo -u postgres createuser --superuser "$USER"   # once: lets the setup script act as you
```

Then set `DATABASE_URL` in `.env` (copy the line from `.env.example` and choose a password), and run:

```sh
scripts/db-setup
```

It creates the role and database the URL names, sets the role's password to the one in the URL, and applies the migrations. It's safe to rerun, and rerunning it is how you apply new migrations after a pull. It only works on a database on this machine, and it refuses a superuser role or the example password. It needs psql 15 or later: if another Postgres is linked (`psql --version`), unlink it first.

When the API starts, it checks the connection. If it can't connect, it logs an error under `groundline.db`, saying where and why but never the password, and keeps running. Public `/health` remains available. With a valid database URL, the API keeps a pooled async engine so protected requests can recover when PostgreSQL becomes available; it disposes that engine on shutdown. Restart after correcting a missing or invalid `DATABASE_URL`.

Point `DATABASE_URL` at the primary database. Authentication reads use READ COMMITTED and require a read-write server; they do not use cached validation results or a read replica that could lag behind revocation ([ADR 0011](docs/adr/0011-api-key-authentication-checks-the-database.md)). Connection pooling reuses connections, not query results.

The tables are defined in [src/groundline/db/tables.py](src/groundline/db/tables.py), and every change needs an Alembic migration to match:

```sh
uv run alembic revision -m "add api_keys.owner_id"   # then fill in upgrade() and downgrade()
scripts/db-setup                                      # apply it
```

A test checks that the migrations and `tables.py` agree.

## API-key module

The [APIKeys module](src/groundline/api_keys.py) is importable independently of the CLI and FastAPI. Its async `create`, `list_all`, `revoke`, and `authenticate` methods accept a caller-owned async engine through the constructor. For example, inside an async function:

```python
from datetime import timedelta

from groundline.api_keys import APIKeys, CreatedAPIKey
from groundline.db.connection import make_engine
from groundline.settings import Settings


async def issue_api_key() -> CreatedAPIKey:
    engine = make_engine(Settings().require_database_url())
    try:
        return await APIKeys(engine).create(
            name="integration", expires_in=timedelta(days=90)
        )
    finally:
        await engine.dispose()
```

The creation result has public `info` and a redacted `secret`; access the plaintext deliberately with `result.secret.get_secret_value()`. It is returned only after commit. `revoke(UUID(...))` returns metadata and a `changed` flag; `authenticate(credential)` returns metadata or `None`. Neither returns the stored hash. Invalid input, unknown identifiers, and storage failures have separate exception types with safe messages.

`await keys.list_all()` returns all `APIKeyInfo` records ordered by creation time descending, with UUID descending breaking ties. It selects only public metadata, never hashes, and returns an empty list when there are no keys. Stored status remains `active` or `revoked`; the CLI derives its `expired` display from the expiry timestamp. Existing names remain readable even if longer than the 128-character limit applied to new custom names.

Use the shared engine factory for primary-database selection and hidden SQL parameters. Keep an engine within its owning event loop; the CLI creates and disposes one inside each command's `asyncio.run`. The API reuses one throughout its lifespan. API keys contain 256 random bits and are hashed with SHA-256 ([ADR 0010](docs/adr/0010-api-keys-are-hashed-with-sha-256.md)). Names are labels, not unique identifiers. Expired API keys retain their stored active/revoked status but cannot authenticate, and validation does not update `last_used_at`.

## Protecting an endpoint

Add the dependency explicitly to a route registered on the existing app. This is an integration example, not a currently shipped endpoint:

```python
from typing import Annotated

from fastapi import Depends

from groundline.api_keys import APIKeyInfo
from groundline.app import app
from groundline.auth import require_api_key


@app.get("/example")
async def example(
    api_key: Annotated[APIKeyInfo, Depends(require_api_key)],
) -> dict[str, str]:
    return {"api_key_id": str(api_key.id)}
```

`POST /v1/omissions` uses `dependencies=[Depends(require_api_key)]` on its route decorator because it needs authentication without using the returned metadata. The example above shows how a handler can use that metadata.

The dependency declares Bearer security in OpenAPI and returns safe API-key metadata. Missing, malformed, unknown, revoked, and expired credentials produce the same 401 response with `WWW-Authenticate: Bearer`. Storage failures produce a generic 503, with no credentials or database error details exposed. Leave public routes such as `/health` without this dependency; it is not global middleware. FastAPI parses JSON before dependencies: broken JSON retains 422 and non-UTF-8 bodies retain 400 even without credentials. For readable JSON, authentication rejects credentials before field validation or evaluation.

## Tests

Run the tests. These are offline, free, and need no real API key; model calls use a scripted fake client, and authenticated HTTP callers use fake API-key storage with the real authentication dependency:

```sh
make test
```

Run the database tests, which need PostgreSQL 18 running locally as described in [Database](#database). Each run creates a throwaway role and database, and drops them afterwards. They're excluded from the default run and CI; run them explicitly:

```sh
uv run pytest -m db
```

API-key coverage includes real create/revoke commands, commit failure, concurrent revocation, expiry, and authentication through two running application instances. Shared fixtures in `tests/conftest.py` create throwaway databases; they never use the developer's `DATABASE_URL`. `tests/test_auth.py` checks the shared dependency on a test-only route. `tests/test_api_omissions_auth.py` exercises the production omissions route offline, including rejected requests doing no pipeline/model work and OpenAPI security. `tests/test_api_omissions_auth_db.py` runs that route with its real lifespan and PostgreSQL, proving acceptance, CLI revocation, unknown credentials, and expiry with a fake model client. Run the database suite explicitly when changing API-key behavior: the default suite and CI exclude it.

Run the live smoke tests, which call the real Anthropic API on short examples: the omission preset, the commission preset and the `/v1/omissions` and `/v1/commissions` endpoints (still about $0.09 per run in all, as the commission calls add under a cent). They're excluded from the default run:

```sh
uv run pytest -m live -s
```

The live omissions endpoint smoke test uses fake API-key storage and a Bearer credential with the real authentication dependency, so it needs no PostgreSQL database. The marked database tests cover real credential persistence and revocation.

`tests/test_parity.py` (omissions) and `tests/test_parity_commissions.py` (commissions) each replay a recorded run of a measured pipeline and check that groundline sends byte-identical prompts and reaches identical results. If you change a prompt, the rubric or a step's logic, expect them to fail. That means the `omission_all_opus` or `commission_quick` preset no longer matches the measured results.

`tests/test_commission_prompts.py` also pins both commission prompts to omission-pipeline's versions by hash, including the refute prompt, which the preset leaves switched off and so no parity run exercises.

## Editor schemas

Editors check the YAML files against JSON schemas, which each file names on its first line:

- profiles (`profile.yaml`) against [profile.schema.json](src/groundline/profiles/profile.schema.json), generated from the profile model;
- pipeline specs and presets against [pipeline.schema.json](src/groundline/presets/pipeline.schema.json), generated from the registry, so each stage's settings are checked against its implementation. A setting's docstring becomes its description.

After changing the profile model, adding an implementation, or changing an implementation's settings, regenerate them (a test fails until you do):

```sh
uv run python -m groundline.profiles.schema
uv run python -m groundline.presets.schema
```

## Lint, format and type checks

Run everything CI runs: [ruff](https://docs.astral.sh/ruff/), [pyright](https://microsoft.github.io/pyright/) in strict mode, the large-file check and the tests. This doesn't change any files:

```sh
make check
```

Apply ruff's lint fixes and formatting:

```sh
make fix
```

The `pre-commit` hook runs the checks in [.pre-commit-config.yaml](.pre-commit-config.yaml) on staged files. If ruff fails, run `make fix` and stage the result. Pyright errors need fixing by hand; run `uv run pyright` to check types on their own.

## Layout

```
Dockerfile                 the app image: a uv build stage and a slim runtime stage
src/groundline/
  core/                    the pipeline framework: steps, registry, specs, runner, cache, costs
  clients/                 the Anthropic client
  db/                      the database: tables, migrations, connection check, setup
  evaluators/              what the checks share: severity, the alarm's output, report helpers
  evaluators/omissions/    the omission check's data models, stages and report
  evaluators/commissions/  the commission check's data models, stages and report
  profiles/clinical/       the clinical profile: prompts and severity rubric
  presets/                 validated pipeline specs (omission_all_opus, commission_quick)
  evaluate.py              evaluate(): the entry point the CLI and API share
  api_keys.py              API-key generation, persistence, revocation and validation
  auth.py                  opt-in Bearer authentication dependency
  keys_cli.py              the groundline keys subgroup
  cli.py                   the groundline command
  api/                     the API's endpoints, with their own request and response models
  app.py                   the FastAPI app
scripts/db-setup           creates the local database and applies migrations
scripts/docker-smoke-test  starts the image and checks /health (make docker-smoke)
tests/                     offline tests; test_live.py is marked live, test_db_schema.py is marked db
```

## Vocabulary and decisions

[CONTEXT.md](CONTEXT.md) is the glossary: the project's words for things, and the words to avoid. [docs/adr/](docs/adr/) records the decisions that would surprise a newcomer, and why they were made. Read both before changing the design. The rules for updating them are in [AGENTS.md](AGENTS.md#domain-language-and-decisions).

## Commits

Commit message rules are in [AGENTS.md](AGENTS.md#commits) (Conventional Commits, e.g. `feat(parser): support nested groups`).

The `commit-msg` hook adds a `Co-authored-by` line for every team member except the person committing. It recognises you by `git config user.email`, so make sure that matches your email in [.githooks/commit-msg](.githooks/commit-msg). To change the team, edit the list at the bottom of that file.

## Upgrading uv

`required-version` in [pyproject.toml](pyproject.toml) allows uv 0.13.7 or later, below 0.14. In case of a new minor release (e.g. 0.14):

1. In pyproject.toml, set `required-version = ">=0.14.0,<0.15"` and, in `[build-system]`, `requires = ["uv_build>=0.14.0,<0.15.0"]`. Keep the two on the same minor: if they differ, uv downloads a separate `uv_build` from the `[build-system]` range to build groundline, in Docker too. Dependabot leaves `uv_build`'s minor alone for the same reason.
2. Update the [Dockerfile](Dockerfile)'s first `FROM` line to the new uv tag and digest. Dependabot doesn't open these PRs, because it can't see `required-version`. Get the digest with `docker buildx imagetools inspect ghcr.io/astral-sh/uv:<version>-python3.13-trixie-slim`.
3. Upgrade your uv to a version in the new range, naming it explicitly, because a plain upgrade installs the newest uv, which may be past the range:
   - Installed with the standalone installer: `uv self update 0.14.0`.
   - Installed with Homebrew: `brew upgrade uv`, then check `uv --version`. If it's past the range, see Troubleshooting. (`uv self update` doesn't work on a Homebrew install.)
   - Installed with pip: `pip install 'uv>=0.14,<0.15'`.
4. Run `make check` and `make docker-smoke`, then push.
5. After merging, tell the team to upgrade uv too (step 3).

**Troubleshooting:** `Required uv version ... does not match the running version` after `brew upgrade` means Homebrew installed a uv newer than the project allows. Until the project moves to it, run `brew uninstall uv`, then install a release within `required-version` with the standalone installer, e.g. `curl -LsSf https://astral.sh/uv/0.13.7/install.sh | sh`.
