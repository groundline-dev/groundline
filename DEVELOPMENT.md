# Developing groundline

How to work on groundline itself. For what it does and how to use it, start with the [README](README.md).

## Getting started

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 (0.13.7 or later) and [Docker](https://docs.docker.com/get-started/get-docker/), installed and running.

1. Install the dependencies and enable the shared git hooks:

   ```sh
   make setup
   ```

2. Copy `.env.example` to `.env`. Fill in `POSTGRES_PASSWORD` and `GROUNDLINE_DB_PASSWORD`, each from `openssl rand -hex 24`. Add `ANTHROPIC_API_KEY` if you'll make live model calls.

3. Start Postgres on `127.0.0.1:5433`, then create the app's database and apply the migrations:

   ```sh
   make db
   scripts/db-setup
   ```

4. Start the API with auto-reload at http://127.0.0.1:8000 (interactive docs at `/docs`):

   ```sh
   make dev
   ```

5. Run the tests and checks before you push:

   ```sh
   make check
   ```

After a pull, rerun `scripts/db-setup` to apply new migrations.

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

## Database

Postgres holds API keys only, never sources, summaries or results ([ADR 0009](docs/adr/0009-the-database-holds-no-patient-data.md)). The CLI and Python checks don't need it; the API and `groundline keys` do.

To change a table, edit [src/groundline/db/tables.py](src/groundline/db/tables.py), then add a migration to match (a test fails until they agree):

```sh
uv run alembic revision -m "add api_keys.owner_id"   # then fill in upgrade() and downgrade()
scripts/db-setup                                      # apply it
```

- **Changing `GROUNDLINE_DB_PASSWORD`:** change it in `.env` and rerun `scripts/db-setup`.
- **Changing `POSTGRES_PASSWORD`:** changing `.env` isn't enough, because Postgres reads it only when the volume is created. Also run `docker compose exec postgres psql -U postgres -c "ALTER ROLE postgres PASSWORD 'NEW'"`.
- **Starting over:** `docker compose down --volumes` deletes the database, with every API key in it.
- **Backing up and restoring:**

  ```sh
  docker compose exec -T postgres pg_dump -U postgres -Fc groundline > groundline.dump
  docker compose exec -T postgres pg_restore -U postgres --clean --if-exists -d groundline < groundline.dump
  ```

## Tests

`make test` runs the offline tests. They're free and don't need an Anthropic API key or Postgres.

Two more suites are excluded from the default run:

- **Database tests** (`uv run pytest -m db`) need `make db` running. Run them when you change API keys or authentication. They create and drop their own throwaway database.
- **Live tests** (`uv run pytest -m live -s`) call the real Anthropic API, for about $0.09 a run.

If you change a prompt, the rubric or a step's logic, expect `tests/test_parity.py` and `tests/test_parity_commissions.py` to fail. They replay recorded runs of the measured presets, so a failure means `omission_all_opus` or `commission_quick` no longer matches its measured results.

## Lint, format and type checks

`make check` runs everything CI runs, without changing files: ruff, pyright in strict mode, the large-file check and the tests. `make fix` applies ruff's fixes and formatting. Pyright errors need fixing by hand.

The `pre-commit` hook runs the same checks on staged files. If ruff fails, run `make fix` and stage the result.

## Editor schemas

The YAML profiles, pipeline specs and presets are checked against generated JSON schemas. After changing the profile model or a stage implementation's settings, regenerate them (a test fails until you do):

```sh
uv run python -m groundline.profiles.schema
uv run python -m groundline.presets.schema
```

## Protecting an endpoint

To require an API key on a route, add `dependencies=[Depends(require_api_key)]` from `groundline.auth`, as `POST /v1/omissions` does. To use the key's metadata in the handler, take it as a parameter instead: `api_key: Annotated[APIKeyInfo, Depends(require_api_key)]`. Leave public routes such as `/health` without it.

## Layout

```
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
scripts/                   db-setup, and the stack check behind make docker-smoke
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
