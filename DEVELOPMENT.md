# Developing groundline

How to work on groundline itself. For what it does and how to use it, start with the [README](README.md).

## Setup

Follow the README's [Setup](README.md#setup) section first: `make setup`, and an API key in `.env` if you'll make live calls. `make setup` installs the dependencies and enables the shared git hooks in [.githooks/](.githooks/).

## Running the API

Start the API with auto-reload at http://127.0.0.1:8000 (interactive docs at `/docs`):

```sh
make dev
```

## Database

The API keeps its operational data, starting with API keys, in PostgreSQL 18. It never holds sources, summaries or results ([ADR 0007](docs/adr/0007-the-database-holds-no-patient-data.md)). The omission check and the `groundline` command don't use it. For now each developer runs Postgres natively; Docker is planned.

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

`tests/test_parity.py` replays a recorded run of the measured pipeline and checks that groundline sends byte-identical prompts and reaches identical results. If you change a prompt, the rubric or a step's logic, expect it to fail. That means the `omission_all_opus` preset no longer matches the measured results.

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
  core/                  the pipeline framework: steps, registry, specs, runner, cache, costs
  clients/               the Anthropic client
  db/                    the database: tables, migrations, connection check, setup
  evaluators/omissions/  the omission check's data models, stages and report
  profiles/clinical/     the clinical profile: prompts and severity rubric
  presets/               validated pipeline specs (omission_all_opus.yaml)
  evaluate.py            evaluate(): the entry point the CLI and API share
  cli.py                 the groundline command
  app.py                 the FastAPI app
scripts/db-setup         creates the local database and applies migrations
tests/                   offline tests; test_live.py is marked live, test_db_schema.py is marked db
```

## Vocabulary and decisions

[CONTEXT.md](CONTEXT.md) is the glossary: the project's words for things, and the words to avoid. [docs/adr/](docs/adr/) records the decisions that would surprise a newcomer, and why they were made. Read both before changing the design, and update them as you go: the `/grill-with-docs` skill from the Skills For Real Engineers plugin does this during a design session.

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), e.g. `feat(parser): support nested groups`.

The `commit-msg` hook adds a `Co-authored-by` line for every team member except the person committing. It recognises you by `git config user.email`, so make sure that matches your email in [.githooks/commit-msg](.githooks/commit-msg). To change the team, edit the list at the bottom of that file.
