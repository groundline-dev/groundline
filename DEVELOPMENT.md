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

Build the image, start it and check that `/health` returns 200. CI runs the same check on every PR. Run it before pushing if you changed the Dockerfile, `.dockerignore`, the dependencies, or how the app starts. It runs on a free port Docker picks, so it works while `make dev` is running:

```sh
make docker-smoke
```

## Database

The API keeps its operational data, starting with API keys, in PostgreSQL 18. It never holds sources, summaries or results ([ADR 0009](docs/adr/0009-the-database-holds-no-patient-data.md)). The omission check and the `groundline` command don't use it. For now each developer runs Postgres natively; Docker is planned.

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

When the API starts, it checks the connection. If it can't connect, it logs an error under `groundline.db`, saying where and why but never the password, and keeps running.

The tables are defined in [src/groundline/db/tables.py](src/groundline/db/tables.py), and every change needs an Alembic migration to match:

```sh
uv run alembic revision -m "add api_keys.owner_id"   # then fill in upgrade() and downgrade()
scripts/db-setup                                      # apply it
```

A test checks that the migrations and `tables.py` agree.

## Tests

Run the tests. These are offline, free, and need no API key; model calls use a scripted fake client:

```sh
make test
```

Run the database tests, which need PostgreSQL 18 running locally as described in [Database](#database). Each run creates a throwaway role and database, and drops them afterwards. They're excluded from the default run, and from CI until the move to Docker:

```sh
uv run pytest -m db
```

Run the live smoke test, which calls the real API on a short example (about $0.04 per run). It's excluded from the default run:

```sh
uv run pytest -m live -s
```

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
  cli.py                   the groundline command
  app.py                   the FastAPI app
scripts/db-setup           creates the local database and applies migrations
scripts/docker-smoke-test  starts the image and checks /health (make docker-smoke)
tests/                     offline tests; test_live.py is marked live, test_db_schema.py is marked db
Dockerfile                 the app image: a uv build stage and a slim runtime stage
```

## Vocabulary and decisions

[CONTEXT.md](CONTEXT.md) is the glossary: the project's words for things, and the words to avoid. [docs/adr/](docs/adr/) records the decisions that would surprise a newcomer, and why they were made. Read both before changing the design, and update them as you go: the `/grill-with-docs` skill from the Skills For Real Engineers plugin does this during a design session.

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), e.g. `feat(parser): support nested groups`.

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
